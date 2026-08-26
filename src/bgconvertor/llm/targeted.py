"""Targeted P2 recovery beyond parent/child hierarchy sums.

Quarterly checks, registry identities, invalid OCR codes, and conflicting
duplicates each have a deterministic acceptance gate.  The model only reads
pixels; it never receives the equation it must satisfy.  Every arithmetic
acceptance requires complete independent readings for all participating rows.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field
from rapidfuzz import fuzz

from ..model import BudgetDocument, BudgetLine, Issue
from ..parsing import NumberParseError, normalize_indicator_code, parse_ro_number
from ..sums import BASE_TOLERANCE

log = logging.getLogger("bgc.llm.targeted")

TRIMS = ("trim1", "trim2", "trim3", "trim4")
ESTIMATED_PAGE_PIXELS = 2_100_000


class EvidenceCell(BaseModel):
    column: str = Field(description="Numele coloanei, exact ca în lista cerută")
    value: str | None = Field(description="Textul tipărit, X sau null dacă este ilizibil")


class EvidenceRow(BaseModel):
    row_id: str = Field(description="Identificatorul rândului, exact ca în cerere")
    code: str = Field(description="Codul indicator exact cum este tipărit")
    cells: list[EvidenceCell] = Field(default_factory=list)


class EvidenceReading(BaseModel):
    rows: list[EvidenceRow]
    note: str = ""


@dataclass(frozen=True)
class TargetRow:
    row_id: str
    line: BudgetLine


@dataclass(frozen=True)
class TargetedJob:
    key: str
    kind: str
    page: int
    rows: tuple[TargetRow, ...]
    columns: tuple[str, ...]
    issues: tuple[Issue, ...]
    coefficients: tuple[Decimal, ...]
    benefit_units: float

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({row.line.page for row in self.rows}))


EVIDENCE_PROMPT = """\
Imaginea conține rânduri dintr-un buget local românesc. Recitește STRICT ce
este tipărit pentru coloanele {columns}. Nu calcula, nu deduce și nu încerca să
faci valorile să respecte vreo regulă.

Rânduri cerute (păstrează row_id exact):
{rows}

Returnează fiecare row_id exact o singură dată. Pentru fiecare rând returnează
fiecare coloană cerută exact o singură dată. Celulă goală, acoperită sau
ilizibilă = null.
"""

CODE_PROMPT = """\
Imaginea conține un rând dintr-un buget local românesc. Recitește numai codul
indicator tipărit pentru rândul de mai jos. Nu îl corecta după denumire și nu
inventa segmente lipsă.

{rows}

Păstrează row_id exact. Lasă cells gol.
"""


def collect_targeted_jobs(
    doc: BudgetDocument,
    registry,
    *,
    job_key_prefix: str = "",
) -> list[TargetedJob]:
    jobs: list[TargetedJob] = []
    jobs.extend(_row_checksum_jobs(doc, job_key_prefix))
    jobs.extend(_identity_jobs(doc, registry, job_key_prefix))
    jobs.extend(_code_jobs(doc, job_key_prefix))
    jobs.extend(_duplicate_jobs(doc, job_key_prefix))
    return jobs


def estimate_targeted_candidates(
    doc: BudgetDocument,
    registry,
    llm_config,
    *,
    job_key_prefix: str = "",
):
    from .ledger import estimate_request_cost
    from .planner import RecoveryCandidate

    candidates = []
    for job in collect_targeted_jobs(doc, registry, job_key_prefix=job_key_prefix):
        prompt = _job_prompt(job)
        max_tokens = _max_tokens(job)
        primary = _primary_model(llm_config, job.kind)
        pixels = ESTIMATED_PAGE_PIXELS * min(3, len(job.pages))
        primary_cost = estimate_request_cost(
            primary, len(prompt), max_tokens, image_pixels=pixels
        )
        premium = (
            llm_config.premium_model
            if llm_config.premium_model
            and llm_config.premium_model != primary
            and job.benefit_units >= llm_config.premium_min_benefit_units
            else None
        )
        premium_cost = (
            estimate_request_cost(
                premium, len(prompt), max_tokens, image_pixels=pixels
            )
            if premium else 0.0
        )
        candidates.append(RecoveryCandidate(
            key=job.key,
            kind=job.kind,
            page=job.page,
            benefit_units=job.benefit_units,
            estimated_cost_usd=primary_cost + premium_cost,
            estimated_calls=2 if premium else 1,
            detail=(
                f"{len(job.rows)} complete independent rows; "
                f"{len(job.columns)} columns"
                + (f"; {premium} reserved only after cheap failure" if premium else "")
            ),
        ))
    return candidates


def repair_targeted(
    doc: BudgetDocument,
    registry,
    client,
    page_image_fn,
    *,
    allowed_job_keys: set[str] | None = None,
    job_key_prefix: str = "",
) -> list[Issue]:
    """Execute non-hierarchy P2 jobs selected by the file-wide planner."""
    from .ledger import BudgetExceeded

    logs: list[Issue] = []
    jobs = collect_targeted_jobs(doc, registry, job_key_prefix=job_key_prefix)
    if allowed_job_keys is not None:
        jobs = [job for job in jobs if job.key in allowed_job_keys]
    jobs.sort(key=lambda job: (-job.benefit_units, job.page, job.key))

    llm_config = getattr(getattr(client, "config", None), "llm", None)
    for job in jobs:
        # A prior duplicate repair can remove a row referenced by a later job.
        if any(row.line not in doc.lines for row in job.rows):
            continue
        primary = _primary_model(llm_config, job.kind) if llm_config else None
        premium = None
        if primary:
            from .escalation import premium_after_failure

            premium = premium_after_failure(client, primary, job.benefit_units)
        try:
            reading = _read_job(client, job, page_image_fn, primary)
            evidence = _verify_job(job, reading, doc, registry)
            used_model = primary
            escalated = False
            if evidence is None and premium:
                reading = _read_job(client, job, page_image_fn, premium)
                evidence = _verify_job(job, reading, doc, registry)
                used_model = premium
                escalated = True
        except BudgetExceeded as exc:
            logs.append(Issue(
                check="V6_repair", severity="warning", page=job.page,
                message=f"{job.kind} stopped: {exc}",
            ))
            break
        except Exception as exc:  # noqa: BLE001 - one job must not abort conversion
            log.warning("%s failed on p%s: %r", job.kind, job.page, exc)
            logs.append(Issue(
                check="V6_repair", severity="warning", page=job.page,
                message=f"{job.kind} call failed ({type(exc).__name__}) — UNRESOLVED",
            ))
            continue

        if evidence is None:
            logs.append(Issue(
                check="V6_repair", severity="warning", page=job.page,
                code=job.rows[0].line.code,
                message=f"{job.kind}: incomplete/inconsistent independent reading — UNRESOLVED",
            ))
            continue

        source = f"llm:{used_model}" if used_model else "llm"
        _apply_job(job, evidence, doc, source)
        logs.append(Issue(
            check="V6_repair", severity="info", page=job.page,
            code=job.rows[0].line.code,
            message=(
                f"{job.kind}: deterministic acceptance gate passed — applied"
                + (f" after premium escalation to {used_model}" if escalated else "")
            ),
        ))
    return logs


def _row_checksum_jobs(doc: BudgetDocument, prefix: str) -> list[TargetedJob]:
    jobs = []
    for index, line in enumerate(doc.lines):
        issues = tuple(issue for issue in line.issues if issue.check == "V3_row_checksum")
        if not issues or line.code is None:
            continue
        jobs.append(TargetedJob(
            key=f"{prefix}quarter:p{line.page}:{index}:{line.code}",
            kind="row_checksum",
            page=line.page,
            rows=(TargetRow("r1", line),),
            columns=("total", *TRIMS),
            issues=issues,
            coefficients=(),
            benefit_units=5.0,
        ))
    return jobs


def _identity_jobs(doc: BudgetDocument, registry, prefix: str) -> list[TargetedJob]:
    if registry is None:
        return []
    jobs = []
    for index, line in enumerate(doc.lines):
        for issue in [issue for issue in line.issues if issue.check == "V5_identity"]:
            if issue.column is None:
                continue
            relation = _identity_relation(doc, registry, line, issue)
            if relation is None:
                continue
            lines, coefficients = relation
            jobs.append(TargetedJob(
                key=(
                    f"{prefix}identity:p{line.page}:{index}:{line.code}:"
                    f"{issue.column}"
                ),
                kind="global_identity",
                page=line.page,
                rows=tuple(
                    TargetRow(f"r{row_index + 1}", relation_line)
                    for row_index, relation_line in enumerate(lines)
                ),
                columns=(issue.column,),
                issues=(issue,),
                coefficients=tuple(coefficients),
                benefit_units=float(len(lines)),
            ))
    return jobs


def _identity_relation(doc, registry, target, issue):
    # Cross-section identity generated directly by the validator.
    if "TOTAL" in issue.message and "FUNCTIONARE" in issue.message:
        key = (target.kind, target.code, target.func_code)
        by_section = {
            line.section: line
            for line in doc.lines
            if (line.kind, line.code, line.func_code) == key
        }
        if all(section in by_section for section in ("TOTAL", "FUNCTIONARE", "DEZVOLTARE")):
            return (
                [by_section["TOTAL"], by_section["FUNCTIONARE"], by_section["DEZVOLTARE"]],
                [Decimal(1), Decimal(-1), Decimal(-1)],
            )

    for identity in registry.identities:
        if identity.scope not in ("revenue", "expense_functional"):
            continue
        rule = identity if doc.suffix == "02" else identity.resuffix(doc.suffix)
        if target.code != rule.target or target.kind != identity.scope:
            continue
        by_code = {
            line.code: line
            for line in doc.lines
            if line.section == target.section and line.kind == target.kind and line.code
        }
        terms = [rule.target, *rule.plus, *rule.minus]
        if not all(code in by_code for code in terms):
            continue
        lines = [by_code[rule.target], *[by_code[code] for code in rule.plus],
                 *[by_code[code] for code in rule.minus]]
        coefficients = [Decimal(1)] + [Decimal(-1)] * len(rule.plus) + [Decimal(1)] * len(rule.minus)
        return lines, coefficients
    return None


def _code_jobs(doc: BudgetDocument, prefix: str) -> list[TargetedJob]:
    jobs = []
    for index, line in enumerate(doc.lines):
        issues = tuple(issue for issue in line.issues if issue.check == "V1_code")
        if not issues:
            continue
        jobs.append(TargetedJob(
            key=f"{prefix}code:p{line.page}:{index}:{line.raw_code or line.code}",
            kind="misread_code",
            page=line.page,
            rows=(TargetRow("r1", line),),
            columns=(),
            issues=issues,
            coefficients=(),
            benefit_units=1.5,
        ))
    return jobs


def _duplicate_jobs(doc: BudgetDocument, prefix: str) -> list[TargetedJob]:
    jobs = []
    seen: dict[tuple, BudgetLine] = {}
    for index, line in enumerate(doc.lines):
        if line.code is None or line.kind == "heading":
            continue
        key = (doc.context_id, line.section, line.kind, line.func_code, line.code)
        previous = seen.get(key)
        seen.setdefault(key, line)
        issues = tuple(
            issue for issue in line.issues
            if issue.check == "V7_hygiene" and "duplicate" in issue.message
        )
        if previous is None or not issues:
            continue
        columns = tuple(sorted(set(previous.values) | set(line.values)))
        if not columns:
            continue
        jobs.append(TargetedJob(
            key=f"{prefix}duplicate:p{line.page}:{index}:{line.code}",
            kind="conflicting_duplicate",
            page=line.page,
            rows=(TargetRow("first", previous), TargetRow("second", line)),
            columns=columns,
            issues=issues,
            coefficients=(),
            benefit_units=float(2 * len(columns)),
        ))
    return jobs


def _job_prompt(job: TargetedJob) -> str:
    rows = "\n".join(
        f"- {row.row_id}: pagina {row.line.page}, cod OCR {row.line.raw_code or row.line.code}, "
        f"denumire {row.line.name[:80]!r}, secțiune {row.line.section or '-'}"
        for row in job.rows
    )
    if job.kind == "misread_code":
        return CODE_PROMPT.format(rows=rows)
    return EVIDENCE_PROMPT.format(
        columns=", ".join(f'"{column}"' for column in job.columns),
        rows=rows,
    )


def _max_tokens(job: TargetedJob) -> int:
    return min(12000, max(1024, 512 + 220 * len(job.rows) * max(1, len(job.columns))))


def _primary_model(llm_config, kind: str) -> str:
    if kind in ("misread_code", "conflicting_duplicate"):
        return llm_config.cell_model
    return llm_config.repair_model


def _read_job(client, job, page_image_fn, model):
    from .orchestrate import _stack_images

    image = _stack_images([page_image_fn(page) for page in job.pages[:3]])
    return client.structured(
        "targeted_repair",
        _job_prompt(job),
        EvidenceReading,
        model=model,
        image=image,
        page=job.page,
        max_tokens=_max_tokens(job),
    )


def _verify_job(job, reading, doc, registry):
    rows = {}
    for row in reading.rows:
        if row.row_id in rows:
            return None
        rows[row.row_id] = row
    if set(rows) != {target.row_id for target in job.rows}:
        return None

    if job.kind == "misread_code":
        candidate = normalize_indicator_code(rows[job.rows[0].row_id].code)
        return candidate if _valid_code_candidate(doc, job.rows[0].line, candidate, registry) else None

    values: dict[str, dict[str, Decimal]] = {}
    for target in job.rows:
        row = rows[target.row_id]
        expected_code = normalize_indicator_code(target.line.code or target.line.raw_code)
        if normalize_indicator_code(row.code) != expected_code:
            return None
        row_values = {}
        for column in job.columns:
            cells = [cell for cell in row.cells if cell.column == column]
            if len(cells) != 1 or cells[0].value in (None, "X"):
                return None
            try:
                parsed = parse_ro_number(cells[0].value, ocr=True)
            except NumberParseError:
                return None
            if not isinstance(parsed, Decimal):
                return None
            row_values[column] = parsed
        values[target.row_id] = row_values

    if job.kind == "row_checksum":
        row = values[job.rows[0].row_id]
        if abs(row["total"] - sum((row[column] for column in TRIMS), Decimal(0))) > BASE_TOLERANCE * 4:
            return None
    elif job.kind == "global_identity":
        for column in job.columns:
            delta = sum(
                coefficient * values[target.row_id][column]
                for target, coefficient in zip(job.rows, job.coefficients, strict=True)
            )
            if abs(delta) > BASE_TOLERANCE * max(2, len(job.rows)):
                return None
    elif job.kind == "conflicting_duplicate":
        first, second = job.rows
        if values[first.row_id] != values[second.row_id]:
            return None
    return values


def _valid_code_candidate(doc, line, candidate, registry) -> bool:
    if candidate is None or registry is None:
        return False
    from ..validate import FORMULA_RE, NAME_THRESHOLD, _fold, _lookup

    trial = line.model_copy(update={"code": candidate})
    entry, is_rollup = _lookup(trial, doc, registry)
    if entry is None and not is_rollup:
        return False
    if entry is not None:
        printed = _fold(FORMULA_RE.sub("", line.name)).strip()
        if fuzz.token_set_ratio(printed, _fold(entry.name).strip()) < NAME_THRESHOLD:
            return False
    return not any(
        other is not line
        and other.section == line.section
        and other.kind == line.kind
        and other.func_code == line.func_code
        and other.code == candidate
        for other in doc.lines
    )


def _apply_job(job, evidence, doc, source):
    if job.kind == "misread_code":
        line = job.rows[0].line
        line.code = evidence
        line.raw_code = evidence.replace(".", "")
        line.code_source = source
        for issue in job.issues:
            if issue in line.issues:
                line.issues.remove(issue)
        return

    for target in job.rows:
        line = target.line
        for column, value in evidence[target.row_id].items():
            line.set_value_with_source(column, value, source)

    if job.kind == "conflicting_duplicate":
        duplicate = job.rows[1].line
        if duplicate in doc.lines:
            doc.lines.remove(duplicate)
        return

    for issue in job.issues:
        for target in job.rows:
            if issue in target.line.issues:
                target.line.issues.remove(issue)
