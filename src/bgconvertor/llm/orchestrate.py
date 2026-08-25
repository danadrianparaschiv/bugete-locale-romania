"""Validator-driven repair orchestration.

Strategy: each V4_hierarchy breach names a parent and its children for one
column — one structured vision call re-reads that whole row-set from the
page image, and the repair is ACCEPTED only if the re-read values make the
sum identity hold. Unparseable cells (V7) are re-read the same way. After
MAX_PASSES the remaining cells stay flagged UNRESOLVED — never guessed.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from pydantic import BaseModel, Field

from ..model import BudgetDocument, Issue
from ..parsing import NumberParseError, parse_ro_number
from ..sums import BASE_TOLERANCE

log = logging.getLogger("bgc.llm.repair")

MAX_GROUP_CALLS = 200  # per document; the dollar budget is the real governor
ESTIMATED_PAGE_PIXELS = 2_100_000
ESTIMATED_CROP_PIXELS = 800_000


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
    allowed_job_keys: set[str] | None = None,
    job_key_prefix: str = "",
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

    # Phase A: collect all repair jobs up front (cheap, sequential)
    jobs, by_code = _collect_sum_jobs(doc)

    jobs.sort(key=lambda job: (-_sum_job_benefit(job), job[0].page, _sum_job_key(job)))
    ledger = getattr(client, "ledger", None)
    concurrency = getattr(getattr(client, "config", None), "llm", None)
    if allowed_job_keys is not None:
        jobs = [
            job
            for job in jobs
            if _qualified_sum_job_key(job, job_key_prefix) in allowed_job_keys
        ]
    elif ledger is not None and concurrency is not None and jobs:
        from .ledger import estimate_request_cost
        from .planner import RecoveryCandidate, select_candidates

        model = concurrency.repair_model
        batch_pricing = bool(concurrency.batch) and len(jobs) >= 4
        image_pixels = ESTIMATED_CROP_PIXELS if row_locator is not None else ESTIMATED_PAGE_PIXELS
        candidates = []
        for job in jobs:
            _line, _broken, group, missing, columns = job
            prompt = _group_prompt(group, missing, columns, labels)
            max_tokens = _group_max_tokens(len(group) + len(missing), len(columns))
            candidates.append(RecoveryCandidate(
                key=_qualified_sum_job_key(job, job_key_prefix),
                kind="sum_repair",
                page=job[0].page,
                benefit_units=_sum_job_benefit(job),
                estimated_cost_usd=estimate_request_cost(
                    model,
                    len(prompt),
                    max_tokens,
                    image_pixels=image_pixels,
                    batch=batch_pricing,
                ),
                detail=f"{len(group)} observed rows, {len(missing)} missing, "
                f"{len(columns)} broken columns",
            ))
        plan = select_candidates(
            candidates,
            ledger.remaining_cost_usd,
            max_calls=ledger.remaining_calls,
        )
        jobs_by_key = {
            _qualified_sum_job_key(job, job_key_prefix): job
            for job in jobs
        }
        jobs = [jobs_by_key[candidate.key] for candidate in plan.selected]
        if plan.skipped:
            repair_log.append(Issue(
                check="V6_repair",
                severity="info",
                page=plan.skipped[0].page,
                message=(
                    f"budget planner selected {len(plan.selected)} sum groups "
                    f"(~${plan.estimated_cost_usd:.3f} reserved worst-case) and skipped "
                    f"{len(plan.skipped)} lower-yield groups"
                ),
            ))

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
            max_tokens=_group_max_tokens(len(group) + len(missing), len(columns)),
        )

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
                "max_tokens": _group_max_tokens(len(group) + len(missing), len(columns)),
                "benefit_units": _sum_job_benefit(job),
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
            repair_model = getattr(concurrency, "repair_model", None)
            applied, recovered = _apply_if_consistent(
                group, missing, reading, column,
                repair_source=f"llm:{repair_model}" if repair_model else "llm",
            )
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


def repair_unparseable(
    doc: BudgetDocument,
    client,
    page_image_fn,
    allowed_job_keys: set[str] | None = None,
    job_key_prefix: str = "",
) -> list[Issue]:
    """Re-read cells the OCR merged/garbled (V7 unparseable).

    Unlike sum repair there is usually no arithmetic constraint to prove the
    reading against, so applied values are marked 'unverified' (info issue)
    and keep source='llm' — honest provenance over false certainty.
    """
    from ..llm.ledger import BudgetExceeded

    repair_log: list[Issue] = []
    by_page = _collect_unparseable_pages(doc)

    ordered_pages = sorted(
        by_page,
        key=lambda page: (
            -sum(len(broken) for _, broken in by_page[page]),
            page,
        ),
    )
    ledger = getattr(client, "ledger", None)
    cheap = getattr(getattr(client, "config", None), "llm", None)
    if allowed_job_keys is not None:
        ordered_pages = [
            page
            for page in ordered_pages
            if _cell_job_key(page, job_key_prefix) in allowed_job_keys
        ]
    elif ledger is not None and cheap is not None and ordered_pages:
        from .planner import RecoveryCandidate, select_candidates

        candidates: list[RecoveryCandidate] = estimate_unparseable_candidates(
            doc,
            cheap,
            job_key_prefix=job_key_prefix,
        )
        plan = select_candidates(
            candidates,
            ledger.remaining_cost_usd,
            max_calls=ledger.remaining_calls,
        )
        ordered_pages = [candidate.page for candidate in plan.selected]
        if plan.skipped:
            repair_log.append(Issue(
                check="V6_repair",
                severity="info",
                page=plan.skipped[0].page,
                message=(
                    f"budget planner selected {len(plan.selected)} unparseable-cell pages "
                    f"and skipped {len(plan.skipped)} lower-confidence pages"
                ),
            ))

    calls = 0
    for page in ordered_pages:
        entries = by_page[page]
        if calls >= MAX_UNPARSEABLE_CALLS:
            break
        lines = [ln for ln, _ in entries][:20]  # keep one prompt readable
        columns = sorted({i.column for _, broken in entries for i in broken})
        cell_model = getattr(cheap, "cell_model", None)
        try:
            reading = _read_group(
                client,
                page_image_fn(page),
                lines,
                [],
                columns,
                {},
                model=cell_model,
                max_tokens=_group_max_tokens(len(lines), len(columns)),
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
                    ln.source = f"llm:{cell_model}" if cell_model else "llm"
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


def _collect_unparseable_pages(doc: BudgetDocument) -> dict[int, list[tuple]]:
    by_page: dict[int, list[tuple]] = {}
    for line in doc.lines:
        broken = [
            issue
            for issue in line.issues
            if issue.check == "V7_hygiene"
            and issue.column
            and "unparseable" in issue.message
        ]
        if broken and line.code:  # code-less rows cannot be matched back reliably
            by_page.setdefault(line.page, []).append((line, broken))
    return by_page


def estimate_unparseable_candidates(
    doc: BudgetDocument,
    llm_config,
    *,
    job_key_prefix: str = "",
):
    """Expose lower-confidence cell rereads to the file-wide planner."""
    from .ledger import estimate_request_cost
    from .planner import RecoveryCandidate

    candidates = []
    for page, entries in _collect_unparseable_pages(doc).items():
        lines = [line for line, _broken in entries][:20]
        columns = sorted({issue.column for _, broken in entries for issue in broken})
        prompt = _group_prompt(lines, [], columns, {})
        max_tokens = _group_max_tokens(len(lines), len(columns))
        broken_cells = sum(len(broken) for _, broken in entries)
        candidates.append(RecoveryCandidate(
            key=_cell_job_key(page, job_key_prefix),
            kind="unparseable_cell",
            page=page,
            benefit_units=0.25 * broken_cells,
            estimated_cost_usd=estimate_request_cost(
                llm_config.cell_model,
                len(prompt),
                max_tokens,
                image_pixels=ESTIMATED_PAGE_PIXELS,
            ),
            detail=f"{broken_cells} unparseable cells; unverified transcription tier",
        ))
    return candidates


def _cell_job_key(page: int, prefix: str) -> str:
    return f"{prefix}cell:p{page}"


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


from ..validate import formula_children  # noqa: E402 — moved; kept for callers


def _collect_sum_jobs(doc: BudgetDocument):
    by_code = {}
    for line in doc.lines:
        if line.code is not None and line.kind != "heading":
            by_code.setdefault((line.section, line.kind, line.func_code, line.code), line)

    jobs = []
    for line in list(doc.lines):
        broken = {
            issue.column: issue
            for issue in line.issues
            if issue.check == "V4_hierarchy" and issue.column is not None
        }
        if not broken:
            continue
        if len(jobs) >= MAX_GROUP_CALLS:
            break
        children = [
            child
            for key, child in by_code.items()
            if key[:3] == (line.section, line.kind, line.func_code)
            and _parent_of(key[3], line.kind) == line.code
        ]
        if not children:
            continue
        # Printed formulas expose children OCR dropped. They are included in
        # both the quality benefit and the arithmetic acceptance gate.
        formula = formula_children(line.name)
        observed = {child.code for child in children}
        missing = [code for code in (formula or []) if code not in observed]
        jobs.append((line, broken, [line, *children], missing, sorted(broken)))
    return jobs, by_code


def estimate_sum_repair_candidates(
    doc: BudgetDocument,
    llm_config,
    *,
    row_crops_available: bool = True,
    column_labels: dict[str, str] | None = None,
    job_key_prefix: str = "",
):
    """Expose file-wide sum-repair candidates to the CLI planner."""
    from .ledger import estimate_request_cost
    from .planner import RecoveryCandidate

    labels = column_labels or {}
    image_pixels = ESTIMATED_CROP_PIXELS if row_crops_available else ESTIMATED_PAGE_PIXELS
    jobs, _by_code = _collect_sum_jobs(doc)
    candidates = []
    for job in jobs:
        _line, _broken, group, missing, columns = job
        prompt = _group_prompt(group, missing, columns, labels)
        max_tokens = _group_max_tokens(len(group) + len(missing), len(columns))
        candidates.append(RecoveryCandidate(
            key=_qualified_sum_job_key(job, job_key_prefix),
            kind="sum_repair",
            page=job[0].page,
            benefit_units=_sum_job_benefit(job),
            estimated_cost_usd=estimate_request_cost(
                llm_config.repair_model,
                len(prompt),
                max_tokens,
                image_pixels=image_pixels,
            ),
            detail=f"{len(group)} observed rows, {len(missing)} missing, "
            f"{len(columns)} broken columns",
        ))
    return candidates


def _group_prompt(group, missing: list[str], columns: list[str], labels: dict) -> str:
    rows = [f"- {ln.code} → {ln.name[:60]}" for ln in group]
    rows += [f"- {code} → (rând nerecunoscut de OCR — caută-l în imagine)" for code in missing]
    return GROUP_PROMPT.format(
        column_labels=", ".join(f'"{labels.get(c, c)}"' for c in columns),
        row_list="\n".join(rows),
    )


def _group_max_tokens(n_rows: int, n_columns: int) -> int:
    return min(12000, max(2048, 512 + 220 * max(1, n_rows) * max(1, n_columns)))


def _sum_job_key(job) -> str:
    line = job[0]
    return "|".join((
        "sum",
        str(line.page),
        line.section or "",
        line.kind or "",
        line.func_code or "",
        line.code or line.raw_code or "",
    ))


def _qualified_sum_job_key(job, prefix: str) -> str:
    """Keep file-wide planner keys unique when documents share page/code context."""
    return f"{prefix}{_sum_job_key(job)}"


def _sum_job_benefit(job) -> float:
    _line, _broken, group, missing, columns = job
    # Arithmetic-proven repairs carry full weight. Missing printed rows add
    # extra value because one accepted call can restore both recall and sums.
    return len(columns) * (len(group) + 1.5 * len(missing))


def _read_group(
    client, image, group, missing: list[str], columns: list[str], labels: dict,
    model: str | None = None,
    max_tokens: int | None = None,
) -> RowSetReading:
    rows = [f"- {ln.code} → {ln.name[:60]}" for ln in group]
    rows += [f"- {code} → (rând nerecunoscut de OCR — caută-l în imagine)" for code in missing]
    prompt = GROUP_PROMPT.format(
        column_labels=", ".join(f'"{labels.get(c, c)}"' for c in columns),
        row_list="\n".join(rows),
    )
    return client.structured(
        "repair", prompt, RowSetReading, image=image, page=group[0].page,
        max_tokens=max_tokens or _group_max_tokens(len(group) + len(missing), len(columns)),
        model=model,
    )


def _apply_if_consistent(
    group, missing: list[str], reading: RowSetReading, column: str,
    repair_source: str = "llm",
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
            ln.source = repair_source
    recovered = [
        BudgetLine(
            code=code, raw_code=code.replace(".", ""), name="(rând recuperat de LLM)",
            page=parent.page, values={column: v}, source=repair_source,
        )
        for code, v in missing_vals.items()
        if v != 0
    ]
    return True, recovered
