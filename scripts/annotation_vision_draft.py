"""Create independent, cached vision drafts for exhaustive annotation.

The helper intentionally reads only the source PDF. It never opens extraction
artifacts, converted workbooks, analysis bundles, or annotation suggestions.
Its JSON output is a draft, not ground truth: a reviewer must still compare it
with the rendered source and freeze it through ``bgconvertor annotate``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from bgconvertor.config import RunConfig, project_root
from bgconvertor.llm.client import LLMClient
from bgconvertor.llm.fallback import FALLBACK_PROMPT, PageReading
from bgconvertor.llm.ledger import Ledger
from bgconvertor.llm.presets import apply as apply_preset
from bgconvertor.orchestrator import parse_pages
from bgconvertor.profilepdf import page_count, render_page


class InventoryReading(BaseModel):
    page_kind: Literal["budget_table", "other_table", "not_relevant", "uncertain"]
    orientation: Literal[0, 90, 180, 270] = Field(
        description="Rotirea anti-orară necesară pentru ca pagina să fie dreaptă"
    )
    source_unit: Literal["lei", "mii_lei", "unknown"]
    table_family: str = Field(
        description="Tip scurt: buget anual, detaliu economic, investiții, HCL etc."
    )
    columns: list[str] = Field(
        description="Coloanele numerice tipărite, în ordine; listă goală dacă nu există"
    )
    note: str = ""


INVENTORY_PROMPT = """\
Clasifică această pagină din dosarul bugetar al unei primării românești. Citește
exclusiv imaginea; nu presupune ce a fost pe alte pagini și nu calcula valori.

page_kind:
- budget_table: tabel de venituri/cheltuieli cu indicatori bugetari și credite
  ori prevederi numerice care trebuie convertite în Excel;
- other_table: investiții, achiziții, personal/salarii, listă de proiecte sau
  altă anexă tabelară care nu este grila bugetară normalizată;
- not_relevant: hotărâre/proză, semnături sau pagină fără tabel numeric relevant;
- uncertain: imaginea nu permite o decizie sigură.

Raportează orientarea anti-orară necesară pentru citire, unitatea exact tipărită,
familia tabelului și etichetele coloanelor numerice în ordinea tipărită. Pentru
continuări fără antet, lasă columns gol în loc să inventezi etichete. Acesta este
doar inventar source-only, nu extracție și nu ground truth.
"""


def _load_project_env() -> None:
    env_file = project_root() / ".env"
    if not env_file.exists():
        return
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw and not raw.startswith("#") and "=" in raw:
            key, _, value = raw.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Draft independent table readings from source PDF images."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument(
        "--columns",
        help="Comma-separated semantic numeric columns in printed order.",
    )
    parser.add_argument(
        "--inventory-only",
        action="store_true",
        help="Classify page/layout/columns without transcribing numeric rows.",
    )
    parser.add_argument("--pages", help="Page selection, for example 1-10 or 3,7.")
    parser.add_argument(
        "--rotation",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="Counter-clockwise image rotation after rendering.",
    )
    parser.add_argument("--scale", type=float, default=2.0)
    parser.add_argument("--model-preset", default="google:gemini-3.6-flash")
    parser.add_argument("--max-cost", type=float, default=5.0)
    parser.add_argument("--max-calls", type=int, default=200)
    parser.add_argument("--max-tokens", type=int, default=16000)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    source = args.source.resolve()
    if not source.is_file():
        raise SystemExit(f"source PDF not found: {source}")
    columns = [
        column.strip() for column in (args.columns or "").split(",") if column.strip()
    ]
    if not args.inventory_only and not columns:
        raise SystemExit("at least one semantic column is required")

    _load_project_env()
    config = RunConfig()
    preset = apply_preset(config, args.model_preset)
    config.llm.max_cost_usd = args.max_cost
    config.llm.max_calls = args.max_calls
    output_dir = args.output_dir.resolve()
    ledger = Ledger(output_dir / "ledger.jsonl", args.max_cost, args.max_calls)
    client = LLMClient(config, ledger, output_dir / "cache")
    selected = parse_pages(args.pages, page_count(source))
    source_hash = _source_sha256(source)
    prompt = INVENTORY_PROMPT if args.inventory_only else FALLBACK_PROMPT.format(
        columns=", ".join(f'"{column}"' for column in columns)
    ) + (
        "\nAceasta este o citire independentă pentru ground truth. "
        "Nu calcula, nu completa prin formule și nu omite rânduri numerice. "
        "Returnează fiecare coloană cerută; folosește null numai când celula "
        "tipărită este goală sau realmente ilizibilă. Simbolurile precum * "
        "dintr-o coloană îngustă, fără antet, sunt marcaje de rând: ignoră-le, "
        "nu le returna drept valori și nu deplasa valorile numerice între "
        "coloane. Prima sumă numerică de după un astfel de marcaj aparține "
        "primei coloane semantice cerute.\n"
    )

    completed = skipped = 0
    for page in selected:
        output = output_dir / "pages" / f"p{page:04d}.json"
        if output.exists():
            skipped += 1
            print(f"p{page:04d}: cached draft", flush=True)
            continue
        image = render_page(source, page, scale=args.scale)
        if args.rotation:
            image = image.rotate(args.rotation, expand=True)
        schema = InventoryReading if args.inventory_only else PageReading
        reading = client.structured(
            "annotation_independent_draft",
            prompt,
            schema,
            model=preset.repair_model,
            image=image,
            page=page,
            max_tokens=args.max_tokens,
        )
        _write_json(output, {
            "schema_version": 1,
            "status": (
                "source_only_inventory_requires_visual_review"
                if args.inventory_only
                else "machine_draft_requires_visual_review"
            ),
            "source_sha256": source_hash,
            "source_page": page,
            "source_year": args.year,
            "columns": reading.columns if args.inventory_only else columns,
            "mode": "inventory" if args.inventory_only else "transcription",
            "rotation": args.rotation,
            "render_scale": args.scale,
            "model_preset": args.model_preset,
            "model": preset.repair_model,
            "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "reading": reading.model_dump(mode="json"),
        })
        completed += 1
        if args.inventory_only:
            print(
                f"p{page:04d}: {reading.page_kind}, {reading.table_family}; "
                f"run cost ${ledger.run_cost_usd:.4f}",
                flush=True,
            )
        else:
            numeric = sum(
                cell.value is not None
                for row in reading.rows
                for cell in row.cells
            )
            print(
                f"p{page:04d}: {len(reading.rows)} rows, {numeric} numeric cells; "
                f"run cost ${ledger.run_cost_usd:.4f}",
                flush=True,
            )
    print(
        f"done: {completed} created, {skipped} reused; {ledger.summary()}",
        flush=True,
    )


if __name__ == "__main__":
    main()
