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
# Târgu Mureș per-unit annex: the institution page opens with the org name in
# caps followed by its fiscal code (CUI), e.g. "CENTRUL DE CULTURA ... 47113359"
CUI_HEADER_RE = re.compile(
    r"^\s*([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 .,\-\"']{8,90}?)\s+(\d{6,9})\s",
)
_NOT_INSTITUTION = ("TOTAL", "SECTIUNEA", "CHELTUIELI", "VENITURI", "BUGETUL",
                    "DENUMIREA", "ANEXA")
# PMB (București) per-unit annexes: each unit page carries "<Name> - 02<letter>",
# the letter being the funding source (A=local, G=venituri proprii, D=externe)
PMB_INST_RE = re.compile(
    r"([A-ZĂÂÎȘȚ][\w ĂÂÎȘȚăâîșț.,\-'&]{5,70}?)\s*-\s*02([A-Z])\b"
)
PMB_SOURCE = {"A": ("local", "02"), "G": ("own_revenue", "10"),
              "D": ("unknown", "08")}
# per-ordonator pages name the institution before its fiscal code (Vaslui)
COD_FISCAL_RE = re.compile(
    r"([A-ZĂÂÎȘȚ][A-ZĂÂÎȘȚ0-9 .,\-']{5,60}?)\s+COD\s+FISCAL\s+\d{6,9}"
)
INDIVIDUAL_BUDGET_RE = re.compile(r"\bBUGET\s+INDIVIDUAL\b", re.IGNORECASE)
FISCAL_TOKEN_RE = re.compile(r"\b[0-9SOGB]{6,9}\b", re.IGNORECASE)
SECTION_CANON = {
    "SECTIUNEA TOTAL": "TOTAL",
    "SECTIUNEA FUNCTIONARE": "FUNCTIONARE",
    "SECTIUNEA DEZVOLTARE": "DEZVOLTARE",
}
REVENUE_TOTAL_RE = re.compile(r"^0001(\d{2})$")  # 000102 local, 000110 own-revenue
EXPENSE_TOTAL_RAW = {"5002", "5010", "4902", "4910"}
DIN_TOTAL_RE = re.compile(r"Din total capitol")
_FUNCTIONAL_HEADER_RE = re.compile(
    r"\b(CAPITOL(?:UL|UI)?|SUBCAPITOL(?:UL|UI)?|PARAGRAF(?:UL|UI)?)\b",
    re.IGNORECASE,
)
_HEADER_CODE_RE = re.compile(r"\b[0-9SOGB]{4,8}\b", re.IGNORECASE)


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip().upper()


def _canonical_section(label: str | None) -> str | None:
    """Normalize both explicit section rows and formula-bearing headings."""
    if not label:
        return None
    upper = label.upper()
    has_function = "FUNCTIONARE" in upper
    has_development = "DEZVOLTARE" in upper
    if has_function and has_development:
        return "TOTAL"
    if has_function:
        return "FUNCTIONARE"
    if has_development:
        return "DEZVOLTARE"
    return SECTION_CANON.get(upper, label)


def _individual_context(text: str, page: int) -> tuple[str, str] | None:
    """Institution and stable context for a repeated individual-budget form.

    Cluj concatenates hundreds of forms whose official document title is
    identical.  The first page nevertheless carries the institution followed
    by its fiscal code and ``BUGET INDIVIDUAL``.  OCR occasionally reads a
    fiscal-code digit as S/O/G/B, so normalize only that tightly constrained
    token.  The physical start page is an explicit fallback, never a guessed
    institution identity.
    """
    marker = INDIVIDUAL_BUDGET_RE.search(text or "")
    if marker is None:
        return None
    prefix = text[:marker.start()]
    napoca = list(re.finditer(r"\b[A-ZĂÂÎȘȚ]*NAPOCA\b", prefix, re.IGNORECASE))
    subject = prefix[napoca[-1].end():] if napoca else prefix[-180:]
    subject = re.split(r"\bANEX[A-ZĂÂÎȘȚ]*\b", subject, maxsplit=1,
                       flags=re.IGNORECASE)[0]
    fiscal = list(FISCAL_TOKEN_RE.finditer(subject))
    if fiscal:
        token = fiscal[-1]
        raw_id = token.group(0).upper().translate(
            str.maketrans({"S": "5", "O": "0", "G": "6", "B": "8"})
        )
        institution = re.sub(r"\s+", " ", subject[:token.start()]).strip(" -.,;:")
        if len(institution) < 4:
            institution = f"Instituție cu CUI {raw_id}"
        return institution[:100], f"cui:{raw_id}"
    return f"Buget individual de la pagina {page}", f"page:{page}"


def _ocr_indicator_code(raw_code: str | None, registry) -> str | None:
    """Recover a nomenclator code carrying OCR lookalikes/source marker A.

    Cluj prints the funding source directly after the suffix (``54.02A``).
    Common OCR output is ``S4.02A`` or ``G8.02A.15.04``.  Accept the repair
    only when the resulting code exists in the official registry.
    """
    if not raw_code or registry is None or not re.search(r"\d", raw_code):
        return None
    candidate = raw_code.upper().translate(
        str.maketrans({"S": "5", "O": "0", "G": "6", "B": "8"})
    )
    candidate = re.sub(r"(?<=\.\d{2})A(?=\.|$)", "", candidate)
    from .parsing import normalize_indicator_code

    normalized = normalize_indicator_code(candidate)
    return normalized if normalized and registry.exists(normalized) else None


def _header_functional_context(text: str, suffix: str, registry) -> str | None:
    """Read the most specific functional code printed in a page header.

    Economic-detail forms often print only bare economic codes in the table,
    while the functional chapter/subchapter/paragraph appears above it.  OCR
    yields both complete forms (``65020301``) and a short paragraph
    (``0103``) following ``Capitolul 5102``.  Adopt only candidates present
    in the official functional registry.
    """
    if not text or registry is None:
        return None
    from .parsing import normalize_indicator_code

    chapter: str | None = None
    candidates: list[str] = []
    matches = list(_FUNCTIONAL_HEADER_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else match.end() + 100
        window = text[match.end():min(len(text), end)]
        raw_codes = _HEADER_CODE_RE.findall(window)
        label = match.group(1).upper()
        for raw in raw_codes:
            digits = raw.upper().translate(
                str.maketrans({"S": "5", "O": "0", "G": "6", "B": "8"})
            )
            direct = normalize_indicator_code(digits)
            if direct and registry.get("expense_functional", direct) is not None:
                candidates.append(direct)
                if label.startswith("CAPITOL") and len(direct) == 5:
                    chapter = direct
                continue
            if (
                chapter
                and not label.startswith("CAPITOL")
                and len(digits) == 4
            ):
                combined = normalize_indicator_code(chapter.replace(".", "") + digits)
                if combined and registry.get("expense_functional", combined) is not None:
                    candidates.append(combined)
        # A source-less two-digit chapter is uncommon in these headers, but
        # completing it is safe when the registry confirms the result.
        if label.startswith("CAPITOL") and not chapter:
            short = re.match(r"\s*(\d{2})\b", window)
            if short:
                completed = f"{short.group(1)}.{suffix}"
                if registry.get("expense_functional", completed) is not None:
                    chapter = completed
                    candidates.append(completed)
    return max(candidates, key=lambda code: (code.count("."), len(code)), default=None)


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

    for page in pages:
        payload = _pick_payload(store, page)
        if payload is None:
            continue
        is_scanned = payload.get("layout") != "digital_detail"
        meta = _doc_meta(payload.get("text") or "")
        # per-institution budgets (Braila): the page header names the
        # institution — each CHANGE of institution starts a new document,
        # regardless of whether the page repeats a recognizable title
        text = payload.get("text") or ""
        individual_context = _individual_context(text, page)
        inst_m = INSTITUTION_RE.search(text)
        inst = inst_m.group(1).strip()[:60] if inst_m else None
        if inst is None:
            cui_m = CUI_HEADER_RE.match(text)
            if cui_m and not cui_m.group(1).strip().startswith(_NOT_INSTITUTION):
                inst = cui_m.group(1).strip()[:60]
        inst_source = None
        if inst is None:
            pmb_m = PMB_INST_RE.search(text)
            if pmb_m and not pmb_m.group(1).strip().upper().startswith(_NOT_INSTITUTION):
                inst = pmb_m.group(1).strip()[:60]
                inst_source = PMB_SOURCE.get(pmb_m.group(2))
        if inst is None:
            cf_m = COD_FISCAL_RE.search(text)
            if cf_m and not cf_m.group(1).strip().startswith(_NOT_INSTITUTION):
                inst = cf_m.group(1).strip()[:60]
        context_id = None
        if individual_context is not None:
            inst, context_id = individual_context
        if inst and doc is not None and not meta:
            base = doc.title.split(" — ")[0]
            budget, suffix = inst_source or (doc.budget, doc.suffix)
            meta = (base, budget, suffix)
        if meta:
            title, budget, suffix = meta
            if inst:
                title = f"{title} — {inst}"
            # some vendors repeat the document title in every page header
            # (Oradea): an identical title continues the current document.
            # ``BUGET INDIVIDUAL`` is different: every occurrence is the first
            # page of a new institution form, even when the title is identical.
            if (
                doc is None
                or individual_context is not None
                or _norm_title(title) != _norm_title(doc.title)
            ):
                doc = BudgetDocument(
                    title=title, budget=budget, suffix=suffix, pages=[], lines=[],
                    context_id=context_id,
                    institution=inst,
                )
                documents.append(doc)
                section, region, cap_context = None, "heading", None
        if doc is None:
            if not payload.get("lines"):
                continue  # prose pages (HCL) before any budget document
            if is_scanned:
                # scanned annexes rarely repeat a full title — open a document
                doc = BudgetDocument(
                    title=f"Anexa (de la pagina {page})", budget="local",
                    suffix="02", pages=[], lines=[], context_id=f"page:{page}",
                )
                documents.append(doc)
                section, region, cap_context = None, "heading", None
            else:
                log.warning("page %d has lines before any document title — skipped", page)
                continue
        doc.pages.append(page)
        header_context = _header_functional_context(text, doc.suffix, registry)
        # Pitești continuation pages can finish a previous aggregate above a
        # mid-page "DIN CARE: Capitolul ..." detail table.  Delay the new
        # context until that table's TOTAL row instead of assigning the
        # aggregate rows to the following chapter.
        defer_header_context = bool(
            header_context
            and "DIN CARE" in text.upper()
            and any(
                "TOTAL CHELTUIELI" in (source.get("name") or "").upper()
                for source in payload["lines"]
            )
        )
        if header_context is not None and not defer_header_context:
            cap_context = header_context
            region = "expense"

        for source_raw in payload["lines"]:
            raw = dict(source_raw)
            if raw.get("section"):
                canon = SECTION_CANON.get(raw["section"], raw["section"])
                if canon != section:
                    section = canon
                    region = "heading"

            raw_code = raw.get("raw_code") or ""
            name = raw.get("name") or ""
            inferred_section = _canonical_section(name)
            if (
                is_scanned
                and not raw.get("section")
                and
                inferred_section in SECTION_CANON.values()
                and "SECTIUN" in name.upper().replace("Ț", "T").replace("Ţ", "T")
                and inferred_section != section
            ):
                section = inferred_section
            leading_context = (
                _header_functional_context(name, doc.suffix, registry)
                if _FUNCTIONAL_HEADER_RE.match(name.strip()) else None
            )
            if leading_context is not None:
                cap_context = leading_context
                region = "expense"
            if (
                defer_header_context
                and header_context is not None
                and name.upper().startswith("TOTAL CHELTUIELI")
            ):
                cap_context = header_context
                region = "expense"
                defer_header_context = False
            if raw.get("code") is None:
                repaired_code = _ocr_indicator_code(raw_code, registry)
                if repaired_code is not None:
                    raw["code"] = repaired_code
            out_of_scope = payload.get("layout") in (
                "investment_list", "allocations_annex", "annex_other"
            )

            # A physical table row may cross the page break.  Join only
            # complementary fragments on consecutive pages: an identified
            # row without values followed by anonymous values, or a name-only
            # tail followed by code/value cells.  Anything less constrained
            # remains separate rather than risking a false merge.
            previous_line = doc.lines[-1] if doc.lines else None
            consecutive = previous_line is not None and previous_line.page == page - 1
            raw_values = raw.get("values") or {}
            if (
                consecutive
                and previous_line.raw_code
                and not previous_line.values
                and not raw_code
                and not name
                and raw_values
            ):
                fragment = _to_line(
                    raw, page, section, region,
                    "ocr" if is_scanned else "digital",
                    suppress_cell_issues=out_of_scope,
                    annex_data=out_of_scope,
                )
                previous_line.values.update(fragment.values)
                previous_line.x_markers.extend(
                    marker for marker in fragment.x_markers
                    if marker not in previous_line.x_markers
                )
                previous_line.issues.extend(fragment.issues)
                continue
            if (
                consecutive
                and not previous_line.raw_code
                and not previous_line.values
                and previous_line.name
                and "SECTIUNEA" not in previous_line.name.upper()
                and raw_code
                and not name
            ):
                raw["name"] = previous_line.name
                name = previous_line.name
                doc.lines.pop()

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

            line = _to_line(
                raw, page, section, region,
                "ocr" if is_scanned else "digital",
                suppress_cell_issues=out_of_scope,
                annex_data=out_of_scope,
            )
            # PDF-truncated combined codes: '5002.580103' prints as
            # '02.580103' (the generator clips the capitol). Only combined-
            # format documents produce these; the current capitol context
            # disambiguates them from genuine dotted codes like '59.01'.
            if (
                line.func_code is None
                and line.raw_code
                and registry is not None
                and not out_of_scope
                and re.match(rf"^{doc.suffix}\.\d{{2,6}}$", line.raw_code)
                and not (line.code and registry.exists(line.code))
            ):
                from .parsing import normalize_indicator_code

                econ = normalize_indicator_code(line.raw_code.split(".", 1)[1])
                if econ:
                    line.code = econ
                    line.func_code = cap_context
                    line.kind = "expense_economic"
            # vendor code style without the budget suffix: '42.55' means
            # 42.<suffix>.55; '65.00.10' uses '00' as a suffix placeholder,
            # its tail being either functional detail or an economic code
            if (
                registry is not None
                and line.code
                and line.func_code is None
                and not out_of_scope
            ):
                m00 = re.match(r"^(\d{2})\.00(?:\.(.+))?$", line.code)
                m2 = re.match(r"^(\d{2})\.(\d{2})$", line.code)
                if m00:
                    cap = f"{m00.group(1)}.{doc.suffix}"
                    tail = m00.group(2) or ""
                    full_cand = f"{cap}.{tail}" if tail else cap
                    from .parsing import normalize_indicator_code as _nic

                    tail_code = _nic(tail) if tail else None
                    is_func = registry.get("expense_functional", full_cand) is not None
                    is_rev = registry.get("revenue", full_cand) is not None
                    is_econ = tail_code and registry.get("expense_economic", tail_code) is not None
                    tail_parts = tail.split(".") if tail else []
                    if is_rev and region == "revenue":
                        line.code = full_cand
                        line.kind = "revenue"
                    elif is_econ and (not is_func or infer_kind(
                        tail_code, registry, region, None, line.name
                    ) == "expense_economic"):
                        line.code, line.func_code = tail_code, cap
                        line.kind = "expense_economic"
                    elif is_func:
                        line.code = full_cand
                        line.kind = "expense_functional"
                    elif is_rev:
                        line.code = full_cand
                        line.kind = "revenue"
                    elif len(tail_parts) == 2 and registry.get(
                        "expense_functional", f"{cap}.{tail_parts[0]}"
                    ) and registry.get("expense_economic", tail_parts[1]):
                        # '61.00.05.71': subcapitol + economic titlu combined
                        line.code = tail_parts[1]
                        line.func_code = f"{cap}.{tail_parts[0]}"
                        line.kind = "expense_economic"
                    elif not tail:
                        line.code = full_cand
                        line.kind = "expense_functional"
                elif m2 and not registry.exists(line.code):
                    with_suffix = f"{m2.group(1)}.{doc.suffix}.{m2.group(2)}"
                    if registry.exists(with_suffix):
                        line.code = with_suffix
                elif not registry.exists(line.code) and not out_of_scope:
                    # source digit dropped entirely (PMB): 67.03.04 -> 67.02.03.04,
                    # bare capitol 67 -> 67.02 — adopt only if the completed code
                    # has a nomenclator entry whose official name matches the
                    # printed one: investment-list ordinals and row-number
                    # artifacts (Bacău, Arad) look exactly like bare codes
                    m3 = re.match(r"^(\d{2})\.(\d{2}(?:\.\d{2})?)$|^(\d{2})$", line.code)
                    if m3:
                        cand = (f"{m3.group(1)}.{doc.suffix}.{m3.group(2)}"
                                if m3.group(1) else f"{m3.group(3)}.{doc.suffix}")
                        ent = next(
                            (e for k in KIND_ORDER
                             if (e := registry.get(k, cand)) is not None), None,
                        )
                        if ent is not None:
                            from rapidfuzz import fuzz

                            from .validate import FORMULA_RE, _fold

                            printed = _fold(FORMULA_RE.sub("", line.name)).strip()
                            if fuzz.token_set_ratio(printed, _fold(ent.name)) >= 60:
                                line.code = cand

            # registry-driven kind for any line whose code lacks explicit
            # functional context (scans, and digital vendors that print bare
            # economic codes like Oradea's '710101')
            if (
                registry is not None
                and line.code
                and line.func_code is None
                and not out_of_scope
            ):
                prev_kind = next(
                    (prev.kind for prev in reversed(doc.lines) if prev.kind != "heading"), None
                )
                line.kind = infer_kind(line.code, registry, region, prev_kind, line.name)
                if line.kind == "expense_economic" and line.section:
                    m = re.match(r"^(\d{4,10})", line.section)
                    if m:
                        from .parsing import normalize_indicator_code

                        line.func_code = normalize_indicator_code(m.group(1))

            # Once a functional chapter is recognized, the rows below it are
            # expense rows even when the form omits a separate TOTAL
            # CHELTUIELI line.  This is the common Cluj individual-budget form.
            if is_scanned and line.kind == "expense_functional" and line.code:
                region = "expense"
            if (
                is_scanned
                and cap_context
                and line.code
                and line.func_code is None
                and line.kind != "expense_functional"
                and not out_of_scope
                and registry is not None
                and registry.get("expense_economic", line.code) is not None
                and region == "expense"
            ):
                line.kind = "expense_economic"
                line.func_code = cap_context

            # Vaslui-style headings carry the capitol as TEXT, without the
            # source digit: "CAPITOL 51 - ..." / "51.01.03 - Autorități
            # executive" — adopt it as context when the completed code exists
            if line.code is None and registry is not None and region == "expense":
                hm = (re.match(r"^CAPITOL(?:UL)?\s+(\d{2})\b", name)
                      or re.match(r"^(\d{2})((?:\.\d{2}){0,2})\s*[-–—]", name))
                if hm:
                    tail = (hm.group(2) or "").strip(".") if (hm.lastindex or 1) >= 2 else ""
                    cand = f"{hm.group(1)}.{doc.suffix}" + (f".{tail}" if tail else "")
                    if registry.get("expense_functional", cand):
                        cap_context = cand
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
            # A heading can be appended to the previous row by OCR (for
            # example "Alte active fixe Capitolul 5402 ...").  Its context
            # starts with the *next* row, not the row that carries the tail.
            embedded = _FUNCTIONAL_HEADER_RE.search(name)
            if embedded is not None and embedded.start() > 0:
                trailing_context = _header_functional_context(
                    name[embedded.start():], doc.suffix, registry
                )
                if trailing_context is not None:
                    cap_context = trailing_context
                    region = "expense"

    for d in documents:
        _derive_rows_from_formulas(d)
        _repair_repeated_summary_cells(d)
        if registry is not None:
            _fix_misread_codes(d, registry)
        log.info(
            "document %r (%s): pages %d-%d, %d lines",
            d.title[:40], d.budget, d.pages[0], d.pages[-1], len(d.lines),
        )
    return documents


def _summary_identity(line: BudgetLine) -> str | None:
    folded = re.sub(r"[^a-z0-9]+", " ", line.name.lower())
    tokens = set(folded.split())
    if {"total", "cheltuieli"} <= tokens:
        return "total_cheltuieli"
    if {"total", "venituri"} <= tokens:
        return "total_venituri"
    return None


def _repair_repeated_summary_cells(doc: BudgetDocument) -> None:
    """Recover a damaged repeated total from an independently printed copy.

    Acceptance requires the same summary identity on another page and two
    already-read columns with identical values. This is deliberately narrower
    than fuzzy duplicate matching: one coincidental total can never authorize
    a cell replacement.
    """
    grouped: dict[str, list[BudgetLine]] = {}
    for line in doc.lines:
        identity = _summary_identity(line)
        if identity:
            grouped.setdefault(identity, []).append(line)
    for lines in grouped.values():
        for target in lines:
            broken_columns = {
                issue.column
                for issue in target.issues
                if issue.check == "V7_hygiene" and issue.column
            }
            for column in sorted(broken_columns):
                candidates = []
                for candidate in lines:
                    if candidate.page == target.page or column not in candidate.values:
                        continue
                    shared = set(target.values) & set(candidate.values) - {column}
                    exact = sum(
                        target.values[key] == candidate.values[key]
                        for key in shared
                    )
                    if exact >= 2:
                        candidates.append(candidate)
                observed = {candidate.values[column] for candidate in candidates}
                if len(observed) != 1:
                    continue
                target.set_value_with_source(
                    column, observed.pop(), "cross_page_repeat"
                )
                target.issues = [
                    issue for issue in target.issues
                    if not (issue.check == "V7_hygiene" and issue.column == column)
                ]


def _derive_rows_from_formulas(doc) -> None:
    """Deterministic recovery of rows OCR dropped, from printed formulas.

    When a parent's name prints its composition ("Hrana (cod 20.03.01+
    20.03.02)") and exactly ONE cited child row is absent, that child's
    value is the arithmetic residual — determined, not guessed. Two or
    more absents stay for the LLM tier (under-determined)."""
    from .model import Issue
    from .sums import BASE_TOLERANCE
    from .validate import formula_children

    by_key: dict[tuple, BudgetLine] = {}
    for ln in doc.lines:
        if ln.code:
            by_key.setdefault((ln.section, ln.kind, ln.func_code, ln.code), ln)
    inserts = []
    for ln in doc.lines:
        if not ln.code or not ln.values:
            continue
        formula = formula_children(ln.name)
        if not formula or ln.code in formula:
            continue
        ctx = (ln.section, ln.kind, ln.func_code)
        present = [by_key[(*ctx, c)] for c in formula if (*ctx, c) in by_key]
        missing = [c for c in formula if (*ctx, c) not in by_key]
        if len(missing) != 1 or not present:
            continue
        bad_cols = {i.column for i in ln.issues if i.severity == "error"}
        values: dict[str, Decimal] = {}
        n = max(2, len(formula))
        for col, pv in ln.values.items():
            if col in bad_cols:
                continue  # the parent's own value is unproven in this column
            s = sum((p.values.get(col, Decimal(0)) for p in present), Decimal(0))
            residual = pv - s
            if abs(residual) > BASE_TOLERANCE * n:
                values[col] = residual
        if not values:
            continue
        row = BudgetLine(
            code=missing[0], raw_code=missing[0].replace(".", ""),
            name="(rând derivat din formula tipărită)", kind=ln.kind,
            page=ln.page, section=ln.section, func_code=ln.func_code,
            values=values, source="formula",
        )
        row.issues.append(Issue(
            check="V4_hierarchy", severity="info", page=ln.page,
            code=missing[0], column=None,
            message=f"rând absent, derivat aritmetic din formula părintelui {ln.code}",
        ))
        by_key[(*ctx, missing[0])] = row
        inserts.append((ln, row))
    for parent, row in inserts:
        doc.lines.insert(doc.lines.index(parent) + 1, row)


def _fix_misread_codes(doc, registry) -> None:
    """Name-driven correction of OCR-misread economic codes.

    Sibiu: 'Piese de schimb' printed as 20.01.08 (a 6 read as 8) collides
    with the real 20.01.08. When duplicated codes carry different names, a
    line whose name badly mismatches its code's official name but strongly
    matches an ABSENT sibling (same parent) is reassigned to that sibling."""
    from collections import defaultdict

    from rapidfuzz import fuzz

    from .validate import _fold

    groups: dict[tuple, list] = defaultdict(list)
    for ln in doc.lines:
        if ln.kind == "expense_economic" and ln.code:
            groups[(ln.func_code, ln.section, ln.code)].append(ln)
    for (func, section, code), lines in list(groups.items()):
        if len(lines) < 2 or "." not in code:
            continue
        parent = code.rsplit(".", 1)[0]
        for ln in lines:
            own = registry.get("expense_economic", code)
            if own is None or fuzz.token_set_ratio(
                _fold(ln.name), _fold(own.name)) >= 50:
                continue
            best = None
            for sib in registry.children("expense_economic", parent):
                if sib == code or (func, section, sib) in groups:
                    continue
                ent = registry.get("expense_economic", sib)
                score = fuzz.token_set_ratio(_fold(ln.name), _fold(ent.name))
                if score >= 85 and (best is None or score > best[0]):
                    best = (score, sib)
            if best:
                from .model import Issue

                ln.issues.append(Issue(
                    check="V1_code", severity="info", page=ln.page,
                    code=best[1], column=None,
                    message=f"cod OCR corectat după nume: {code} -> {best[1]} "
                            f"({best[0]:.0f}% potrivire cu numele oficial)",
                ))
                ln.code = best[1]
                ln.raw_code = best[1].replace(".", "")
                groups[(func, section, best[1])] = [ln]


def _pick_payload(store: RunStore, page: int):
    """Merge paid recovery into deterministic output by row and cell."""
    det = store.get("extract", page)
    llm = store.get("llm_extract", page)
    if det is not None:
        if det.get("layout") in (
            "investment_list", "allocations_annex", "annex_other", "hcl_prose",
            "official_prose_summary",
        ):
            return det  # deliberately out of scope — an LLM transcription adds noise
        if llm is None:
            return det
    from .llm.fallback import merge_page_payloads

    return merge_page_payloads(det, [llm] if llm is not None else [])


def _to_line(
    raw: dict, page: int, section: str | None, region: str,
    default_source: str = "digital", suppress_cell_issues: bool = False,
    annex_data: bool = False,
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
    if annex_data and (values or x_markers):
        kind = "annex"
    elif code is None:
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
    source = raw.get("source") or default_source
    return BudgetLine(
        issues=line_issues,
        raw_code=raw.get("raw_code"),
        source=source,
        code=code,
        func_code=func_code,
        name=raw.get("name") or "",
        kind=kind,
        row_no=raw.get("row_no"),
        page=page,
        section=section,
        values=values,
        value_sources={
            column: (raw.get("value_sources") or {}).get(column, source)
            for column in values
        },
        x_markers=x_markers,
    )
