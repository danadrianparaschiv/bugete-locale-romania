"""Validator-driven repair orchestration.

Strategy: each V4_hierarchy breach names a parent and its children for one
column — one structured vision call re-reads that whole row-set from the
page image, and the repair is ACCEPTED only if the re-read values make the
sum identity hold. Unparseable cells (V7) are re-read the same way. After
MAX_PASSES the remaining cells stay flagged UNRESOLVED — never guessed.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from pydantic import BaseModel, Field

from ..model import BudgetDocument, Issue
from ..parsing import NumberParseError, parse_ro_number
from ..sums import BASE_TOLERANCE

log = logging.getLogger("bgc.llm.repair")

MAX_GROUP_CALLS = 200  # per document; the dollar budget is the real governor


class CellValue(BaseModel):
    column: str = Field(description="Numele coloanei, exact ca în lista cerută")
    value: str | None = Field(
        description="Exact ce e tipărit în celulă, format românesc "
        "(ex. '58.295'), 'X', sau null dacă e goală/ilizibilă"
    )


class RowReading(BaseModel):
    code: str = Field(description="Codul indicator al rândului, exact cum e tipărit")
    cells: list[CellValue] = Field(description="Una per coloană cerută")


class RowSetReading(BaseModel):
    rows: list[RowReading]
    note: str = Field(default="", description="Observații (ștampilă, rânduri lipite, etc.)")


GROUP_PROMPT = """\
Imaginea este o pagină scanată dintr-un buget local românesc (mii lei, \
format românesc: punct=mii, virgulă=zecimale). Transcrie valorile din \
coloanele {column_labels} pentru rândurile de mai jos.

Rândurile de citit (cod indicator → denumire aproximativă):
{row_list}

Transcrie STRICT ce este tipărit în imagine, celulă cu celulă. NU calcula, \
NU deduce și NU corecta valorile ca să respecte vreo regulă — orice \
verificare aritmetică se face separat, în afara acestui pas. Dacă un rând \
listat nu există în imagine sau celula e goală/acoperită/ilizibilă, pune null.
"""


def repair_document(
    doc: BudgetDocument,
    client,
    page_image_fn,
    column_labels: dict[str, str] | None = None,
    row_locator=None,  # (page, raw_codes) -> (y0_frac, y1_frac) | None
) -> list[Issue]:
    """Attempt LLM repair of sum-breach groups in one document.

    client: LLMClient (or a fake in tests) with .structured(...).
    page_image_fn: page number -> PIL image of the (rotation-corrected) page.
    Returns the list of repair-log issues (info severity) it generated.
    """
    from ..llm.ledger import BudgetExceeded

    labels = column_labels or {}
    repair_log: list[Issue] = []
    calls = 0

    by_code = {}
    for ln in doc.lines:
        if ln.code is not None and ln.kind != "heading":
            by_code.setdefault((ln.section, ln.kind, ln.func_code, ln.code), ln)

    # Phase A: collect all repair jobs up front (cheap, sequential)
    jobs = []
    for line in list(doc.lines):
        broken = {
            i.column: i for i in line.issues
            if i.check == "V4_hierarchy" and i.column is not None
        }
        if not broken:
            continue
        if len(jobs) >= MAX_GROUP_CALLS:
            break
        children = [
            ln for key, ln in by_code.items()
            if key[:3] == (line.section, line.kind, line.func_code)
            and _parent_of(key[3], line.kind) == line.code
        ]
        if not children:
            continue
        # The page often prints the true composition in the parent's name
        # ("(cod 74.02.03+74.02.05+74.02.50)"): rows OCR dropped are asked
        # for too, so an incomplete child set can't fake a consistent sum.
        formula = formula_children(line.name)
        observed = {c.code for c in children}
        missing = [c for c in (formula or []) if c not in observed]
        jobs.append((line, broken, [line, *children], missing, sorted(broken)))

    # Phase B: network reads, in parallel (calls are independent; the ledger
    # is thread-safe and BudgetExceeded short-circuits the remaining jobs)
    def _read(job):
        line, _broken, group, missing, columns = job
        pages = sorted({ln.page for ln in group})[:3]
        images = [page_image_fn(p) for p in pages]
        if row_locator is not None and len(pages) == 1 and images[0] is not None:
            codes = {c for ln in group for c in (ln.raw_code, ln.code) if c}
            band = row_locator(pages[0], codes)
            if band:
                img = images[0]
                y0 = max(0, int((band[0] - 0.03) * img.height))
                y1 = min(img.height, int((band[1] + 0.02) * img.height))
                if y1 - y0 > 40:
                    images = [img.crop((0, y0, img.width, y1))]
        return _read_group(
            client, _stack_images(images),
            group, missing, columns, labels,
        )

    concurrency = getattr(getattr(client, "config", None), "llm", None)
    n_workers = getattr(concurrency, "concurrency", 1) or 1
    use_batch = bool(getattr(concurrency, "batch", False)) and len(jobs) >= 4
    readings: list = []
    if use_batch:
        from .batch import batch_structured

        batch_jobs = []
        for i, job in enumerate(jobs):
            line, _broken, group, missing, columns = job
            pages = sorted({ln.page for ln in group})[:3]
            prompt = _group_prompt(group, missing, columns, labels)
            batch_jobs.append({
                "key": str(i), "purpose": "repair", "prompt": prompt,
                "image": _stack_images([page_image_fn(p) for p in pages]),
                "output_model": RowSetReading, "page": line.page,
            })
        results = batch_structured(client, batch_jobs)
        readings = [results[str(i)] for i in range(len(jobs))]
    elif n_workers > 1 and len(jobs) > 1:
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutTimeout

        # hard per-future deadline: a hung HTTP call must never stall the
        # run for hours (seen live: 1.5h freeze via OpenRouter). Stuck
        # workers are abandoned; the SDK-level timeout reaps them later.
        deadline = getattr(concurrency, "call_deadline_s", 1800) or 1800
        pool = ThreadPoolExecutor(max_workers=n_workers)
        try:
            futures = [pool.submit(_read, job) for job in jobs]
            for fut in futures:
                try:
                    readings.append(fut.result(timeout=deadline))
                except FutTimeout:
                    log.warning("repair call abandoned after %ds without a result",
                                deadline)
                    readings.append(RuntimeError(f"call deadline {deadline}s hit"))
                except Exception as exc:  # noqa: BLE001 - collected per job
                    readings.append(exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    else:
        for job in jobs:
            try:
                readings.append(_read(job))
            except Exception as exc:  # noqa: BLE001
                readings.append(exc)

    # Phase C: apply sequentially (mutates shared document state)
    budget_hit = False
    for (line, broken, group, missing, columns), reading in zip(jobs, readings, strict=False):
        calls += 1
        if isinstance(reading, BudgetExceeded):
            if not budget_hit:
                budget_hit = True
                repair_log.append(Issue(
                    check="V6_repair", severity="warning", page=line.page,
                    message=f"repair stopped: {reading}",
                ))
            continue
        if isinstance(reading, Exception):
            log.warning("repair call failed for %s p%s: %r", line.code, line.page, reading)
            repair_log.append(Issue(
                check="V6_repair", severity="warning", page=line.page, code=line.code,
                message=f"repair call failed ({type(reading).__name__}) — group left UNRESOLVED",
            ))
            continue
        for column in columns:
            applied, recovered = _apply_if_consistent(group, missing, reading, column)
            for new_line in recovered:
                new_line.kind = line.kind
                new_line.section = line.section
                new_line.func_code = line.func_code
                new_line.page = line.page
                doc.lines.append(new_line)
                key = (line.section, line.kind, line.func_code, new_line.code)
                by_code.setdefault(key, new_line)
            repair_log.append(Issue(
                check="V6_repair",
                severity="info" if applied else "warning",
                page=line.page, code=line.code, column=column,
                message=(
                    f"LLM re-read {len(group)} rows for {line.code} {column}: "
                    + ("sum now consistent — applied" if applied else "still inconsistent — UNRESOLVED")
                ),
            ))
            if applied:
                line.issues.remove(broken[column])
    return repair_log


MAX_UNPARSEABLE_CALLS = 100  # per document; the dollar budget still governs


def repair_unparseable(doc: BudgetDocument, client, page_image_fn) -> list[Issue]:
    """Re-read cells the OCR merged/garbled (V7 unparseable).

    Unlike sum repair there is usually no arithmetic constraint to prove the
    reading against, so applied values are marked 'unverified' (info issue)
    and keep source='llm' — honest provenance over false certainty.
    """
    from ..llm.ledger import BudgetExceeded

    repair_log: list[Issue] = []
    by_page: dict[int, list[tuple]] = {}
    for ln in doc.lines:
        broken = [
            i for i in ln.issues
            if i.check == "V7_hygiene" and i.column and "unparseable" in i.message
        ]
        if broken and ln.code:  # code-less rows can't be matched back reliably
            by_page.setdefault(ln.page, []).append((ln, broken))

    calls = 0
    for page, entries in sorted(by_page.items()):
        if calls >= MAX_UNPARSEABLE_CALLS:
            break
        lines = [ln for ln, _ in entries][:20]  # keep one prompt readable
        columns = sorted({i.column for _, broken in entries for i in broken})
        cheap = getattr(getattr(client, "config", None), "llm", None)
        cell_model = getattr(cheap, "cell_model", None)
        try:
            reading = _read_group(
                client, page_image_fn(page), lines, [], columns, {}, model=cell_model
            )
        except BudgetExceeded as exc:
            repair_log.append(Issue(
                check="V6_repair", severity="warning", page=page,
                message=f"unparseable-cell repair stopped: {exc}",
            ))
            break
        except Exception as exc:
            calls += 1
            log.warning("unparseable repair failed p%s: %r", page, exc)
            continue
        calls += 1
        new = {r.code.replace(" ", ""): r for r in reading.rows if r.code}
        fixed = 0
        for ln, broken in entries:
            row = None
            for key in (ln.code, ln.raw_code):
                if key and key in new:
                    row = new[key]
                    break
            if row is None:
                continue
            for issue in broken:
                raw = next((c.value for c in row.cells if c.column == issue.column), None)
                if raw in (None, "X"):
                    continue
                try:
                    parsed = parse_ro_number(raw, ocr=True)
                except NumberParseError:
                    continue
                if isinstance(parsed, Decimal):
                    ln.values[issue.column] = parsed
                    ln.source = "llm"
                    ln.issues.remove(issue)
                    ln.issues.append(Issue(
                        check="V6_repair", severity="info", page=page,
                        code=ln.code, column=issue.column,
                        message="cell re-read from image (unverified transcription)",
                    ))
                    fixed += 1
        repair_log.append(Issue(
            check="V6_repair", severity="info", page=page,
            message=f"unparseable-cell pass p{page}: {fixed} cells recovered",
        ))
    return repair_log


def _stack_images(images):
    """Stack page images vertically (a sum group can span a page break)."""
    images = [im for im in images if im is not None]
    if not images:
        return None
    if len(images) == 1:
        return images[0]
    from PIL import Image

    width = max(im.width for im in images)
    out = Image.new("RGB", (width, sum(im.height for im in images)), "white")
    y = 0
    for im in images:
        out.paste(im.convert("RGB"), (0, y))
        y += im.height
    return out


def _parent_of(code: str, kind: str) -> str | None:
    from ..nomenclator import parent_code

    return parent_code(code, kind)


FORMULA_RE = re.compile(r"\(\s*cod\.?\s*([0-9+.\s]+?la[0-9+.\s]+|[0-9+.\s]+)\)", re.IGNORECASE)


def formula_children(name: str) -> list[str] | None:
    """Codes from a printed formula like '(cod 74.02.03+74.02.05+74.02.50)'.

    Returns None when the formula contains a 'la' range (incomplete
    enumeration) or no formula is present.
    """
    m = FORMULA_RE.search(name)
    if not m:
        return None
    body = m.group(1)
    if "la" in body:
        return None
    codes = [c.strip() for c in body.split("+")]
    codes = [c for c in codes if re.fullmatch(r"\d{2}(\.\d{2}){0,3}", c)]
    return codes or None


def _group_prompt(group, missing: list[str], columns: list[str], labels: dict) -> str:
    rows = [f"- {ln.code} → {ln.name[:60]}" for ln in group]
    rows += [f"- {code} → (rând nerecunoscut de OCR — caută-l în imagine)" for code in missing]
    return GROUP_PROMPT.format(
        column_labels=", ".join(f'"{labels.get(c, c)}"' for c in columns),
        row_list="\n".join(rows),
    )


def _read_group(
    client, image, group, missing: list[str], columns: list[str], labels: dict,
    model: str | None = None,
) -> RowSetReading:
    rows = [f"- {ln.code} → {ln.name[:60]}" for ln in group]
    rows += [f"- {code} → (rând nerecunoscut de OCR — caută-l în imagine)" for code in missing]
    prompt = GROUP_PROMPT.format(
        column_labels=", ".join(f'"{labels.get(c, c)}"' for c in columns),
        row_list="\n".join(rows),
    )
    return client.structured(
        "repair", prompt, RowSetReading, image=image, page=group[0].page,
        max_tokens=12000, model=model,
    )


def _apply_if_consistent(
    group, missing: list[str], reading: RowSetReading, column: str
) -> tuple[bool, list]:
    """Apply the re-read values if the sum holds; returns (applied, new lines).

    Values for `missing` codes (rows OCR dropped, named by the printed
    formula) participate in the sum and, on success, become new lines.
    """
    from ..model import BudgetLine

    new_values: dict[str, Decimal] = {}
    for row in reading.rows:
        raw = next((c.value for c in row.cells if c.column == column), None)
        if raw in (None, "X"):
            continue
        try:
            parsed = parse_ro_number(raw, ocr=True)
        except NumberParseError:
            return False, []
        if isinstance(parsed, Decimal):
            new_values[row.code.replace(" ", "")] = parsed

    def val(ln):
        for key in (ln.code, ln.raw_code):
            if key and key in new_values:
                return new_values[key]
        return ln.values.get(column, Decimal(0))

    parent, children = group[0], group[1:]
    missing_vals = {c: new_values.get(c, Decimal(0)) for c in missing}
    total_children = sum((val(c) for c in children), Decimal(0)) + sum(
        missing_vals.values(), Decimal(0)
    )
    n = max(2, len(children) + len(missing))
    if abs(val(parent) - total_children) > BASE_TOLERANCE * n:
        return False, []

    for ln in group:
        v = val(ln)
        if ln.values.get(column) != v:
            ln.values[column] = v
            ln.source = "llm"
    recovered = [
        BudgetLine(
            code=code, raw_code=code.replace(".", ""), name="(rând recuperat de LLM)",
            page=parent.page, values={column: v}, source="llm",
        )
        for code, v in missing_vals.items()
        if v != 0
    ]
    return True, recovered
