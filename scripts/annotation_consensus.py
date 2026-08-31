"""Compare independent vision drafts without consulting converter output."""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from bgconvertor.parsing import NumberParseError, normalize_indicator_code, parse_ro_number


@dataclass(frozen=True)
class Fact:
    page: int
    row: int
    identity: str
    column: str
    value: str

    @property
    def exact(self) -> tuple[str, str, str]:
        return self.identity, self.column, self.value

    @property
    def key(self) -> tuple[str, str]:
        return self.identity, self.column


def _fold(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", ascii_value.lower()).strip()


def _identity(code: str | None, name: str | None) -> str:
    normalized = normalize_indicator_code(code) if code else None
    if normalized:
        return f"code:{normalized}"
    folded = _fold(name)
    return f"name:{folded}" if folded else "name:<empty>"


def _canonical(value: str | None) -> str | None:
    if value is None or value.strip().lower() in {"", "null"}:
        return None
    try:
        parsed = parse_ro_number(value, ocr=True)
    except NumberParseError:
        return f"unparsed:{value}"
    if parsed in (None, "X"):
        return None if parsed is None else "X"
    decimal = Decimal(parsed)
    if decimal == 0:
        return "0"
    text = format(decimal, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def load_draft(directory: Path) -> dict[int, list[Fact]]:
    pages: dict[int, list[Fact]] = {}
    for path in sorted((directory / "pages").glob("p*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        page = int(payload["source_page"])
        facts = []
        for row_index, row in enumerate(payload["reading"]["rows"], 1):
            identity = _identity(row.get("code"), row.get("name"))
            for cell in row.get("cells") or []:
                value = _canonical(cell.get("value"))
                if value is not None:
                    facts.append(Fact(
                        page=page,
                        row=row_index,
                        identity=identity,
                        column=str(cell["column"]),
                        value=value,
                    ))
        pages[page] = facts
    return pages


def _alignment(primary: dict[int, list[Fact]], mirror: dict[int, list[Fact]]) -> list[dict]:
    """Monotonically align section pages to combined pages.

    Economic codes repeat on many institutional pages, so independent
    best-page matching can jump to the wrong institution. Official section
    PDFs preserve order; dynamic programming uses that constraint and weights
    rare functional/page identities more heavily than generic codes.
    """
    primary_counters = {
        page: Counter(fact.key for fact in facts)
        for page, facts in primary.items()
    }
    key_frequency = Counter(
        key for counter in primary_counters.values() for key in counter
    )
    primary_pages = sorted(primary)
    mirror_pages = [page for page in sorted(mirror) if mirror[page]]
    mirror_counters = {
        page: Counter(fact.key for fact in mirror[page])
        for page in mirror_pages
    }

    def score(mirror_page: int, primary_page: int) -> tuple[float, int]:
        overlap = mirror_counters[mirror_page] & primary_counters[primary_page]
        raw = sum(overlap.values())
        weighted = sum(
            count / key_frequency[key]
            for key, count in overlap.items()
        )
        return weighted, raw

    rows, columns = len(mirror_pages), len(primary_pages)
    negative = float("-inf")
    dp = [[negative] * (columns + 1) for _ in range(rows + 1)]
    decision = [["skip"] * (columns + 1) for _ in range(rows + 1)]
    for column in range(columns + 1):
        dp[0][column] = 0.0
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            best = dp[row][column - 1]
            if dp[row - 1][column - 1] != negative:
                aligned_score = (
                    dp[row - 1][column - 1]
                    + score(mirror_pages[row - 1], primary_pages[column - 1])[0]
                )
                if aligned_score >= best:
                    best = aligned_score
                    decision[row][column] = "align"
            dp[row][column] = best

    chosen: dict[int, int] = {}
    row, column = rows, columns
    while row and column:
        if decision[row][column] == "align":
            chosen[mirror_pages[row - 1]] = primary_pages[column - 1]
            row -= 1
            column -= 1
        else:
            column -= 1

    aligned = []
    for mirror_page in sorted(mirror):
        mirror_counter = Counter(fact.key for fact in mirror[mirror_page])
        primary_page = chosen.get(mirror_page)
        if primary_page is None:
            matched = primary_total = 0
            weighted = 0.0
        else:
            weighted, matched = score(mirror_page, primary_page)
            primary_total = sum(primary_counters[primary_page].values())
        mirror_total = sum(mirror_counter.values())
        aligned.append({
            "mirror_page": mirror_page,
            "primary_page": primary_page,
            "alignment_score": round(weighted, 4),
            "matched": matched,
            "mirror_facts": mirror_total,
            "primary_facts": primary_total,
            "mirror_coverage_pct": (
                round(100 * matched / mirror_total, 2)
                if mirror_total else 100.0
            ),
        })
    return aligned


def _component_match(
    primary_value: str,
    first: Counter[str],
    second: Counter[str],
) -> tuple[str, str] | None:
    expected = Decimal(primary_value)
    for left in list(first):
        if first[left] <= 0:
            continue
        for right in list(second):
            if second[right] <= 0:
                continue
            if Decimal(left) + Decimal(right) == expected:
                first[left] -= 1
                second[right] -= 1
                return left, right
    return None


def compare(primary: dict[int, list[Fact]], mirrors: dict[str, dict[int, list[Fact]]]) -> dict:
    alignments = {
        name: _alignment(primary, pages)
        for name, pages in mirrors.items()
    }
    mirror_by_primary: dict[str, dict[int, list[Fact]]] = {
        name: defaultdict(list) for name in mirrors
    }
    primary_pages_by_key: dict[tuple[str, str], set[int]] = defaultdict(set)
    for primary_page, facts in primary.items():
        for fact in facts:
            primary_pages_by_key[fact.key].add(primary_page)
    for name, alignment in alignments.items():
        pages = mirrors[name]
        for item in alignment:
            preferred = item["primary_page"]
            for fact in pages[item["mirror_page"]]:
                candidates = primary_pages_by_key.get(fact.key, set())
                if len(candidates) == 1:
                    target = next(iter(candidates))
                elif preferred in candidates and item["matched"] >= 3:
                    target = preferred
                else:
                    continue
                mirror_by_primary[name][target].append(fact)

    page_reports = []
    totals = Counter()
    conflicts = []
    for page, primary_facts in sorted(primary.items()):
        values_by_source: dict[str, dict[tuple[str, str], Counter[str]]] = {}
        for name in mirrors:
            by_key: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
            for fact in mirror_by_primary[name].get(page, []):
                by_key[fact.key][fact.value] += 1
            values_by_source[name] = by_key
        source_names = list(mirrors)
        first_name = source_names[0]
        second_name = source_names[1] if len(source_names) > 1 else None
        confirmed_sum = 0
        confirmed_single = 0
        conflicting = 0
        uncovered = 0
        fact_results = []
        for fact in primary_facts:
            first = values_by_source[first_name].get(fact.key, Counter())
            second = (
                values_by_source[second_name].get(fact.key, Counter())
                if second_name else Counter()
            )
            matched = _component_match(fact.value, first, second) if second else None
            if matched is not None:
                confirmed_sum += 1
                fact_results.append({
                    "row": fact.row,
                    "identity": fact.identity,
                    "column": fact.column,
                    "value": fact.value,
                    "status": "sum_confirmed",
                    "components": {
                        first_name: matched[0],
                        second_name: matched[1],
                    },
                })
                continue
            available = [counter for counter in (first, second) if sum(counter.values())]
            # If the row exists in only one section, an exact value is an
            # independently printed confirmation; the absent section is not
            # silently treated as numeric zero.
            if len(available) == 1 and available[0][fact.value] > 0:
                available[0][fact.value] -= 1
                confirmed_single += 1
                fact_results.append({
                    "row": fact.row,
                    "identity": fact.identity,
                    "column": fact.column,
                    "value": fact.value,
                    "status": "single_source_confirmed",
                })
                continue
            if available:
                conflicting += 1
                fact_results.append({
                    "row": fact.row,
                    "identity": fact.identity,
                    "column": fact.column,
                    "value": fact.value,
                    "status": "conflicting",
                })
                conflicts.append({
                    "primary_page": page,
                    "primary_row": fact.row,
                    "identity": fact.identity,
                    "column": fact.column,
                    "primary_value": fact.value,
                    "component_values": {
                        name: sorted(
                            value
                            for value, count in values_by_source[name]
                            .get(fact.key, Counter()).items()
                            if count > 0
                        )
                        for name in source_names
                    },
                })
            else:
                uncovered += 1
                fact_results.append({
                    "row": fact.row,
                    "identity": fact.identity,
                    "column": fact.column,
                    "value": fact.value,
                    "status": "uncovered",
                })
        confirmed = confirmed_sum + confirmed_single
        page_reports.append({
            "page": page,
            "primary_facts": len(primary_facts),
            "confirmed": confirmed,
            "sum_confirmed": confirmed_sum,
            "single_source_confirmed": confirmed_single,
            "conflicting": conflicting,
            "uncovered": uncovered,
            "confirmed_pct": (
                round(100 * confirmed / len(primary_facts), 2)
                if primary_facts else 100.0
            ),
            "facts": fact_results,
        })
        totals.update({
            "primary_facts": len(primary_facts),
            "confirmed": confirmed,
            "sum_confirmed": confirmed_sum,
            "single_source_confirmed": confirmed_single,
            "conflicting": conflicting,
            "uncovered": uncovered,
        })
    return {
        "schema_version": 1,
        "method": (
            "page alignment by fact identity; combined value equals the two "
            "section components, or exactly matches the sole printed section"
        ),
        "totals": dict(totals),
        "page_alignments": alignments,
        "pages": page_reports,
        "conflicts": conflicts,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("primary", type=Path)
    parser.add_argument("--mirror", action="append", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    primary = load_draft(args.primary)
    mirrors = {path.name: load_draft(path) for path in args.mirror}
    report = compare(primary, mirrors)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    totals = report["totals"]
    print(
        f"{totals['confirmed']}/{totals['primary_facts']} exact; "
        f"{totals['conflicting']} conflicts; {totals['uncovered']} uncovered"
    )


if __name__ == "__main__":
    main()
