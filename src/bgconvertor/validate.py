"""Validator: nomenclator checks and the arithmetic backbone.

Checks (see PLAN.md §5):
    V1 code validity        code exists in the right annex for the budget
    V2 name concordance     fuzzy match vs official denumire
    V3 row checksum         total = trim1+trim2+trim3+trim4
    V4 hierarchy sums       parent = sum(children), per column
    V5 identities           revenue cascade, parts, grupe, TOTAL=FUNC+DEZV
    V7 hygiene              duplicates, unparseable cells (added at assembly)

Every failed check becomes an Issue on the line (or the document); the
quality report is an aggregation of these.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal

from rapidfuzz import fuzz

from .model import BudgetDocument, BudgetLine, ConversionResult, Issue
from .nomenclator import Registry, parent_code
from .parsing import format_ro_number
from .sums import BASE_TOLERANCE

FORMULA_RE = re.compile(r"\(\s*cod[^)]*\)?", re.IGNORECASE)
FORMULA_BODY_RE = re.compile(
    r"\(\s*cod\.?\s*([0-9+.\s]+?la[0-9+.\s]+|[0-9+.\s]+)\)", re.IGNORECASE
)


def formula_children(name: str) -> list[str] | None:
    """Codes from a printed formula like '(cod 74.02.03+74.02.05+74.02.50)'.

    Returns None when the formula contains a 'la' range (incomplete
    enumeration) or no formula is present.
    """
    m = FORMULA_BODY_RE.search(name)
    if not m:
        return None
    body = m.group(1)
    if "la" in body:
        return None
    codes = [c.strip() for c in body.split("+")]
    codes = [c for c in codes if re.fullmatch(r"\d{2}(\.\d{2}){0,3}", c)]
    return codes or None
ECON_GRUPE = {"01", "70", "79", "84", "85", "90"}
NAME_THRESHOLD = 55
SECTIONS = ("TOTAL", "FUNCTIONARE", "DEZVOLTARE")


def _fold(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def _strip_suffix(code: str, suffix: str) -> str | None:
    """'00.01.02' -> '00.01' when the trailing segment is the budget suffix."""
    if code.endswith("." + suffix):
        return code[: -(len(suffix) + 1)]
    return None


def validate(result: ConversionResult, registry: Registry) -> ConversionResult:
    for doc in result.documents:
        for line in doc.lines:
            if line.kind in ("heading", "annex") or line.code is None:
                continue
            _check_code_and_name(line, doc, registry)
            _check_row_checksum(line)
        _check_duplicates(doc)
        _check_hierarchy(doc, registry)
        _check_identities(doc, registry, result.issues)
        _check_cross_section(doc)
    return result


def revalidate(result: ConversionResult, registry: Registry) -> ConversionResult:
    """Re-run validators after repair without duplicating stale findings.

    Assembly evidence (unparseable source cells and audited info repairs) and
    V6 repair provenance survive. Validator-generated errors/warnings are
    rebuilt from the mutated document, so a repaired code/value cannot hide a
    newly created hierarchy or identity breach.
    """

    def stale(issue: Issue) -> bool:
        if issue.severity not in ("error", "warning"):
            return False
        if issue.check in {
            "V1_code", "V2_name", "V3_row_checksum", "V4_hierarchy", "V5_identity"
        }:
            return True
        return issue.check == "V7_hygiene" and "duplicate" in issue.message

    result.issues = [issue for issue in result.issues if not stale(issue)]
    for document in result.documents:
        for line in document.lines:
            line.issues = [issue for issue in line.issues if not stale(issue)]
    return validate(result, registry)


# -- V1 + V2 ----------------------------------------------------------------

def _lookup(line: BudgetLine, doc: BudgetDocument, registry: Registry):
    """Return (entry, is_rollup). Rollups are matched with suffix stripping."""
    kind = line.kind
    code = line.code
    upper = line.name.upper()
    # Report-form usages that shadow real Anexa codes: 5002/5010 prints both
    # TOTAL CHELTUIELI and Partea I; "Partea" headings never carry entry names.
    if upper.startswith(("TOTAL CHELTUIELI", "CHELTUIELILE SECTIUNII", "PARTEA ")):
        return None, True
    entry = registry.get(kind, code)
    if entry:
        return entry, False
    # Credit/FEN budgets (.06/.07/.08) share the .02 chapter structure but
    # have no published annex of their own — validate against .02.
    if doc.suffix not in ("02", "10"):
        resuffixed = re.sub(rf"^(\d{{2}})\.{doc.suffix}", r"\1.02", code)
        entry = registry.get(kind, resuffixed)
        if entry:
            return entry, False
    rollups = {r.code for r in registry.rollups}
    if code in rollups:
        return None, True
    stripped = _strip_suffix(code, doc.suffix)
    if stripped and stripped in rollups:
        return None, True
    # Parts and section rollups are defined with .02; map .10 codes onto them.
    if doc.suffix != "02":
        resuffixed = re.sub(rf"^(\d{{2}})\.{doc.suffix}", r"\1.02", code)
        if resuffixed in rollups:
            return None, True
    return None, False


def _check_code_and_name(line: BudgetLine, doc: BudgetDocument, registry: Registry) -> None:
    entry, is_rollup = _lookup(line, doc, registry)
    if entry is None and not is_rollup:
        line.issues.append(
            Issue(
                check="V1_code", severity="error", page=line.page, code=line.code,
                message=f"code {line.code} ({line.kind}) not in nomenclator",
            )
        )
        return
    if entry is None:
        return  # rollup: names vary by form, skip V2
    printed = _fold(FORMULA_RE.sub("", line.name)).strip()
    official = _fold(entry.name).strip()
    score = fuzz.token_set_ratio(printed, official)
    if score < NAME_THRESHOLD:
        line.issues.append(
            Issue(
                check="V2_name", severity="warning", page=line.page, code=line.code,
                message=f"name mismatch vs nomenclator ({score:.0f}%): "
                        f"{line.name[:40]!r} vs {entry.name[:40]!r}",
            )
        )


# -- V3 ---------------------------------------------------------------------

TRIMS = ("trim1", "trim2", "trim3", "trim4")


def _check_row_checksum(line: BudgetLine) -> None:
    if "total" not in line.values or not all(t in line.values for t in TRIMS):
        return
    delta = line.values["total"] - sum(line.values[t] for t in TRIMS)
    if abs(delta) > BASE_TOLERANCE * 4:
        line.issues.append(
            Issue(
                check="V3_row_checksum", severity="error", page=line.page, code=line.code,
                message=f"total != sum(trimestre), delta {format_ro_number(delta)}",
            )
        )


# -- V4 ---------------------------------------------------------------------

def _value_columns(lines: list[BudgetLine]) -> set[str]:
    return {c for ln in lines for c in ln.values}


def _check_hierarchy(doc: BudgetDocument, registry: Registry) -> None:
    groups: dict[tuple, dict[str, BudgetLine]] = defaultdict(dict)
    for ln in doc.lines:
        if ln.kind in ("heading", "annex") or ln.code is None:
            continue
        key = (ln.section, ln.kind, ln.func_code)
        groups[key].setdefault(ln.code, ln)  # first occurrence wins

    for key, by_code in groups.items():
        _, kind, _func = key
        children_of: dict[str, list[BudgetLine]] = defaultdict(list)
        for code, ln in by_code.items():
            parent = parent_code(code, kind)
            if parent and parent in by_code:
                children_of[parent].append(ln)
        for parent, children in children_of.items():
            parent_line = by_code[parent]
            # A native municipal workbook copies numeric cells without OCR.
            # Locally-authored sheets commonly publish selected non-zero
            # descendants rather than a complete hierarchy; their partial sum
            # is coverage evidence, not evidence that the machine-readable
            # parent cell is wrong. Code/name and cross-section checks still run.
            if parent_line.source not in ("native_excel", "official_prose"):
                _compare_sum(parent_line, children, "V4_hierarchy")

        if kind == "expense_economic":
            section, _, func = key
            # grupa 01 = sum of its component titles
            comp = [
                ln for c, ln in by_code.items()
                if c in {"10", "20", "30", "40", "50", "51", "55", "56", "57", "58", "59", "60", "61", "65"}
            ]
            if (
                "01" in by_code
                and comp
                and by_code["01"].source not in ("native_excel", "official_prose")
            ):
                _compare_sum(by_code["01"], comp, "V4_hierarchy")
            # capitol total (functional row) = sum of top-level grupa rows
            # (85 is negative in the classification and sums as printed)
            grupa_lines = [ln for c, ln in by_code.items() if c in ECON_GRUPE]
            cap_line = groups.get((section, "expense_functional", None), {}).get(func)
            if (
                cap_line
                and grupa_lines
                and cap_line.source not in ("native_excel", "official_prose")
            ):
                _compare_sum(cap_line, grupa_lines, "V4_hierarchy")


def _compare_sum(parent: BudgetLine, children: list[BudgetLine], check: str) -> None:
    # Only columns the children actually carry: estimari (2027-2029) are often
    # printed at aggregate level only, so a parent-only column is not a breach.
    columns = {c for ln in children for c in ln.values}
    columns.discard("credite_stinse")
    for col in columns:
        pv = parent.values.get(col, Decimal(0))
        cv = sum((ln.values.get(col, Decimal(0)) for ln in children), Decimal(0))
        if cv == 0 and pv != 0:
            # Reporting convention: estimari (and some trims) are approved at
            # aggregate level only; detail rows print 0,00. Not a breach.
            continue
        if abs(pv - cv) > BASE_TOLERANCE * max(2, len(children)):
            parent.issues.append(
                Issue(
                    check=check, severity="error", page=parent.page,
                    code=parent.code, column=col,
                    message=(
                        f"{parent.code} {col}: parent {format_ro_number(pv)} != "
                        f"sum(children) {format_ro_number(cv)} "
                        f"({len(children)} children)"
                    ),
                )
            )


# -- V5 ---------------------------------------------------------------------

def _identity_values(
    doc: BudgetDocument, section: str | None, kind: str
) -> dict[str, dict[str, Decimal]]:
    """(identity-space code) -> {column: value} for one section and kind.

    Scoping by kind matters: economic articles 60.02/61.02 ("Finantare
    publica nationala") share code strings with functional capitols.
    """
    out: dict[str, dict[str, Decimal]] = {}
    for ln in doc.lines:
        if ln.section != section or ln.code is None or ln.kind != kind:
            continue
        keys = {ln.code}
        stripped = _strip_suffix(ln.code, doc.suffix)
        if stripped:
            keys.add(stripped)
        for k in keys:
            out.setdefault(k, ln.values)
    return out


def _check_identities(doc: BudgetDocument, registry: Registry, extra: list[Issue]) -> None:
    for section in SECTIONS:
        maps = {
            "revenue": _identity_values(doc, section, "revenue"),
            "expense_functional": _identity_values(doc, section, "expense_functional"),
        }
        if not any(maps.values()):
            continue
        for ident in registry.identities:
            if ident.scope in ("expense_economic", "section"):
                # economic identities are per-capitol (handled in hierarchy);
                # 37.02.03 = -37.02.04 is cross-section (Phase 2 of validator)
                continue
            values_by_code = maps[ident.scope]
            ident_local = ident if doc.suffix == "02" else ident.resuffix(doc.suffix)
            terms = [ident_local.target, *ident_local.plus, *ident_local.minus]
            if not all(t in values_by_code for t in terms):
                continue
            # only columns at least one source term carries (estimari are
            # sometimes aggregate-only)
            columns = {
                c
                for t in (*ident_local.plus, *ident_local.minus)
                for c in values_by_code[t]
            } & set(values_by_code[ident_local.target])
            columns.discard("credite_stinse")
            for col in columns:
                target = values_by_code[ident_local.target].get(col, Decimal(0))
                expected = sum(
                    (values_by_code[c].get(col, Decimal(0)) for c in ident_local.plus),
                    Decimal(0),
                ) - sum(
                    (values_by_code[c].get(col, Decimal(0)) for c in ident_local.minus),
                    Decimal(0),
                )
                n = max(2, len(ident_local.plus) + len(ident_local.minus))
                if abs(target - expected) > BASE_TOLERANCE * n:
                    _attach_identity_issue(
                        doc, extra, ident_local, section, col, target, expected
                    )


def _attach_identity_issue(
    doc: BudgetDocument, extra: list[Issue], ident, section, col, target, expected
) -> None:
    """Attach to the target line when it exists, else to document-level issues."""
    severity = "error" if ident.verified else "warning"
    line = next(
        (
            ln for ln in doc.lines
            if ln.section == section
            and (ln.code == ident.target or _strip_suffix(ln.code or "", doc.suffix) == ident.target)
        ),
        None,
    )
    issue = Issue(
        check="V5_identity", severity=severity, code=ident.target, column=col,
        page=line.page if line else None,
        message=(
            f"[{section}] {ident.target} {col}: {format_ro_number(target)} != "
            f"{format_ro_number(expected)} per {ident.note or 'identity'}"
            + ("" if ident.verified else " (identity unverified)")
        ),
    )
    (line.issues if line else extra).append(issue)


# -- cross-section ----------------------------------------------------------

def _check_cross_section(doc: BudgetDocument) -> None:
    """TOTAL section = FUNCTIONARE + DEZVOLTARE, per (kind, code, func) and column."""
    per_section: dict[str, dict[tuple, BudgetLine]] = {s: {} for s in SECTIONS}
    for ln in doc.lines:
        if (
            ln.section in per_section
            and ln.code is not None
            and ln.kind not in ("heading", "annex")
        ):
            per_section[ln.section].setdefault((ln.kind, ln.code, ln.func_code), ln)

    total, func, dezv = (per_section[s] for s in SECTIONS)
    if not func or not dezv:
        return
    for key, t_line in total.items():
        f = func.get(key)
        d = dezv.get(key)
        if f is None or d is None:
            # some rows print without code in one section (e.g. "Partea" rows)
            # — absence is not zero, so only compare fully-present codes
            continue
        columns = set(t_line.values)
        columns.discard("credite_stinse")
        for col in columns:
            tv = t_line.values.get(col, Decimal(0))
            fv = f.values.get(col, Decimal(0)) if f else Decimal(0)
            dv = d.values.get(col, Decimal(0)) if d else Decimal(0)
            if abs(tv - (fv + dv)) > BASE_TOLERANCE * 2:
                t_line.issues.append(
                    Issue(
                        check="V5_identity", severity="error", page=t_line.page,
                        code=t_line.code, column=col,
                        message=(
                            f"{t_line.code} {col}: TOTAL {format_ro_number(tv)} != "
                            f"FUNCTIONARE {format_ro_number(fv)} + "
                            f"DEZVOLTARE {format_ro_number(dv)}"
                        ),
                    )
                )


# -- V7 duplicates ----------------------------------------------------------

def _check_duplicates(doc: BudgetDocument) -> None:
    seen: dict[tuple, BudgetLine] = {}
    for ln in doc.lines:
        if ln.code is None or ln.kind in ("heading", "annex"):
            continue
        # context_id makes the validation scope explicit for repeated forms.
        # Documents are already assembled per context, but retaining it in
        # the key prevents future combined-document callers from regressing.
        key = (
            doc.context_id,
            ln.institution,
            ln.form,
            ln.subdocument,
            ln.section,
            ln.kind,
            ln.func_code,
            ln.code,
        )
        prev = seen.get(key)
        if prev is None:
            seen[key] = ln
        elif prev.values != ln.values:
            ln.issues.append(
                Issue(
                    check="V7_hygiene", severity="warning", page=ln.page, code=ln.code,
                    message=f"duplicate of p{prev.page} with different values",
                )
            )
