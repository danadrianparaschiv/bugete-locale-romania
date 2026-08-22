"""Assemble per-page extraction payloads into BudgetDocuments.

Handles what a single page cannot know: document boundaries (a PDF may
concatenate several budgets), section carry-over across pages, and the
revenue/expense region switch.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal

from .model import BudgetDocument, BudgetLine, Issue
from .runstore import RunStore

log = logging.getLogger("bgc.assemble")

DOC_TITLE_RE = re.compile(r"(BUGETUL\s+[A-Z][A-ZĂÂÎȘȚ ,\-]{10,})")
INSTITUTION_RE = re.compile(r"Institutia publica:\s*([A-ZĂÂÎȘȚ0-9 .,\-\"']{4,}?)(?:\s+Buge|\s*$)")
SECTION_CANON = {
    "SECTIUNEA TOTAL": "TOTAL",
    "SECTIUNEA FUNCTIONARE": "FUNCTIONARE",
    "SECTIUNEA DEZVOLTARE": "DEZVOLTARE",
}
REVENUE_TOTAL_RE = re.compile(r"^0001(\d{2})$")  # 000102 local, 000110 own-revenue
EXPENSE_TOTAL_RAW = {"5002", "5010", "4902", "4910"}
DIN_TOTAL_RE = re.compile(r"Din total capitol")


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().upper()


def _doc_meta(text: str) -> tuple[str, str, str] | None:
    m = DOC_TITLE_RE.search(text or "")
    if not m:
        return None
    title = m.group(1).strip()
    upper = (text or "").upper()
    if "CREDITELOR INTERNE" in title:
        return title, "credite_interne", "07"
    if "CREDITELOR EXTERNE" in title:
        return title, "credite_externe", "06"
    if "FONDURILOR EXTERNE" in title:
        return title, "fen", "08"
    if "GENERAL" in title:
        return title, "general", "02"
    if "PE TITLURI" in title:
        return title, "local", "02"
    if "CENTRALIZAT" in title or "INSTITUTI" in title:
        return title, "own_revenue", "10"
    if "LOCAL" in title:
        return title, "local", "02"
    if "VENITURI PROPRII" in upper:
        # text-based hint only when the title itself is inconclusive
        return title, "own_revenue", "10"
    return title, "unknown", "02"


KIND_ORDER = ("revenue", "expense_functional", "expense_economic")


def infer_kind(
    code: str, registry, region: str, prev_kind: str | None, name: str = ""
) -> str:
    """Registry-driven kind inference for extracted lines.

    Most codes match exactly one classification. Ambiguous ones (51.02 /
    60.02 are both functional capitols and economic articles) are tiebroken
    by the printed NAME against the official names of the candidates —
    "Autoritati publice" picks the capitol, "Transferuri de capital" the
    articol — then by continuity with the previous line, then by region.
    """
    matches = [k for k in KIND_ORDER if registry.get(k, code) is not None]
    if not matches:
        return {"revenue": "revenue", "expense": "expense_functional"}.get(region, "heading")
    if len(matches) == 1:
        return matches[0]

    if name:
        from rapidfuzz import fuzz

        from .validate import FORMULA_RE, _fold

        printed = _fold(FORMULA_RE.sub("", name)).strip()
        scored = sorted(
            ((fuzz.token_set_ratio(printed, _fold(registry.get(k, code).name)), k)
             for k in matches),
            reverse=True,
        )
        if scored[0][0] >= 60 and scored[0][0] - scored[1][0] >= 15:
            return scored[0][1]

    if prev_kind in matches:
        return prev_kind
    region_kind = {"revenue": "revenue", "expense": "expense_functional"}.get(region)
    if region_kind in matches:
        return region_kind
    return matches[0]


def assemble(store: RunStore, pages: list[int], registry=None) -> list[BudgetDocument]:
    documents: list[BudgetDocument] = []
    doc: BudgetDocument | None = None
    section: str | None = None
    region = "heading"  # heading | revenue | expense
    cap_context: str | None = None  # current functional capitol (e.g. "65.02")

    combined_seen = False  # this document prints capitol-combined codes
    for page in pages:
        payload = store.get("llm_extract", page) or store.get("extract", page)
        if payload is None:
            continue
        is_scanned = payload.get("layout") != "digital_detail"
        meta = _doc_meta(payload.get("text") or "")
        # per-institution budgets (Braila): the page header names the
        # institution — each CHANGE of institution starts a new document,
        # regardless of whether the page repeats a recognizable title
        inst_m = INSTITUTION_RE.search(payload.get("text") or "")
        inst = inst_m.group(1).strip()[:60] if inst_m else None
        if inst and doc is not None and not meta:
            base = doc.title.split(" — ")[0]
            meta = (base, doc.budget, doc.suffix)
        if meta:
            title, budget, suffix = meta
            if inst:
                title = f"{title} — {inst}"
            # some vendors repeat the document title in every page header
            # (Oradea): an identical title continues the current document
            if doc is None or _norm_title(title) != _norm_title(doc.title):
                doc = BudgetDocument(
                    title=title, budget=budget, suffix=suffix, pages=[], lines=[]
                )
                documents.append(doc)
                section, region = None, "heading"
                combined_seen = False
        if doc is None:
            if not payload.get("lines"):
                continue  # prose pages (HCL) before any budget document
            if is_scanned:
                # scanned annexes rarely repeat a full title — open a document
                doc = BudgetDocument(
                    title=f"Anexa (de la pagina {page})", budget="local",
                    suffix="02", pages=[], lines=[],
                )
                documents.append(doc)
                section, region = None, "heading"
            else:
                log.warning("page %d has lines before any document title — skipped", page)
                continue
        doc.pages.append(page)

        for raw in payload["lines"]:
            if raw.get("section"):
                canon = SECTION_CANON.get(raw["section"], raw["section"])
                if canon != section:
                    section = canon
                    region = "heading"

            raw_code = raw.get("raw_code") or ""
            name = raw.get("name") or ""
            upper = name.upper()
            if REVENUE_TOTAL_RE.match(raw_code) and "VENITURI" in upper:
                region = "revenue"
            elif "TOTAL VENITURI" in upper and is_scanned:
                region = "revenue"
            elif (
                raw_code in EXPENSE_TOTAL_RAW or is_scanned
            ) and (
                upper.startswith("TOTAL CHELTUIELI") or upper.startswith("CHELTUIELILE SECTIUNII")
            ):
                region = "expense"

            out_of_scope = payload.get("layout") in ("investment_list", "allocations_annex")
            line = _to_line(
                raw, page, section, region,
                "ocr" if is_scanned else "digital",
                suppress_cell_issues=out_of_scope,
            )
            if line.func_code:
                combined_seen = True
            # PDF-truncated combined codes: '5002.580103' prints as
            # '02.580103' (the generator clips the capitol). Only combined-
            # format documents produce these; the current capitol context
            # disambiguates them from genuine dotted codes like '59.01'.
            if (
                line.func_code is None
                and line.raw_code
                and registry is not None
                and re.match(rf"^{doc.suffix}\.\d{{2,6}}$", line.raw_code)
                and not (line.code and registry.exists(line.code))
            ):
                from .parsing import normalize_indicator_code

                econ = normalize_indicator_code(line.raw_code.split(".", 1)[1])
                if econ:
                    line.code = econ
                    line.func_code = cap_context
                    line.kind = "expense_economic"
            # registry-driven kind for any line whose code lacks explicit
            # functional context (scans, and digital vendors that print bare
            # economic codes like Oradea's '710101')
            if registry is not None and line.code and line.func_code is None:
                prev_kind = next(
                    (l.kind for l in reversed(doc.lines) if l.kind != "heading"), None
                )
                line.kind = infer_kind(line.code, registry, region, prev_kind, line.name)
                if line.kind == "expense_economic" and line.section:
                    m = re.match(r"^(\d{4,10})", line.section)
                    if m:
                        from .parsing import normalize_indicator_code

                        line.func_code = normalize_indicator_code(m.group(1))

            # Track capitol context; repair PDF-truncated economic prefixes
            # ("5002.580103" prints as "02.580103" — the generator clips it).
            if (
                line.kind == "expense_functional"
                and line.code
                and len(line.code) == 5
                and not name.startswith("Partea")
            ):
                cap_context = line.code
            if line.kind == "expense_economic" and line.func_code and len(line.func_code) < 5:
                if cap_context and cap_context.endswith("." + line.func_code):
                    line.func_code = cap_context
            if line.kind == "expense_economic" and line.func_code is None:
                line.func_code = cap_context  # bare economic codes: per-capitol grouping
            doc.lines.append(line)

    for d in documents:
        log.info(
            "document %r (%s): pages %d-%d, %d lines",
            d.title[:40], d.budget, d.pages[0], d.pages[-1], len(d.lines),
        )
    return documents


def _to_line(
    raw: dict, page: int, section: str | None, region: str,
    default_source: str = "digital", suppress_cell_issues: bool = False,
) -> BudgetLine:
    values: dict[str, Decimal] = {}
    x_markers: list[str] = []
    for col, v in (raw.get("values") or {}).items():
        if v == "X":
            x_markers.append(col)
        else:
            values[col] = Decimal(v)

    # stored payloads may predate parser improvements: retry their
    # unparseable cells with the CURRENT parser before flagging them
    healed_issues = []
    for ci in raw.get("cell_issues", []):
        from .parsing import NumberParseError, parse_ro_number

        try:
            parsed = parse_ro_number(ci.get("raw"), ocr=True)
        except NumberParseError:
            healed_issues.append(ci)
            continue
        if parsed == "X":
            x_markers.append(ci["column"])
        elif parsed is not None:
            values.setdefault(ci["column"], Decimal(parsed))
    if suppress_cell_issues:
        # out-of-nomenclator annexes (investment lists, allocations): keep
        # the data for side sheets, but their OCR noise is not a quality
        # problem of the BUDGET extraction
        healed_issues = []
    raw = {**raw, "cell_issues": healed_issues}

    code = raw.get("code")
    func_code = raw.get("func_code")
    if code is None:
        kind = "heading"
    elif func_code:
        kind = "expense_economic"
    elif region == "revenue":
        kind = "revenue"
    elif region == "expense":
        kind = "expense_functional"
    else:
        kind = "heading"

    # "Din total capitol NNNN:" headings carry no code but set memo context.
    if DIN_TOTAL_RE.search(raw.get("name") or ""):
        kind = "heading"

    line_issues = [
        Issue(
            check="V7_hygiene",
            severity="error",
            message=f"unparseable cell {ci['raw']!r} in column {ci['column']}",
            page=page,
            column=ci["column"],
        )
        for ci in raw.get("cell_issues", [])
    ]
    return BudgetLine(
        issues=line_issues,
        raw_code=raw.get("raw_code"),
        source=raw.get("source") or default_source,
        code=code,
        func_code=func_code,
        name=raw.get("name") or "",
        kind=kind,
        row_no=raw.get("row_no"),
        page=page,
        section=section,
        values=values,
        x_markers=x_markers,
    )
