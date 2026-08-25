"""Batch API mode: the same structured calls at 50% price, asynchronously.

Used for unattended corpus runs (config.llm.batch): the repair phase's
reads are submitted as one message batch, polled until it ends, and each
result flows through the same cache + ledger as interactive calls — so a
later interactive run replays batch results for free, and vice versa.
"""

from __future__ import annotations

import base64
import logging
import time

from .client import LLMClient, _png_bytes, _strict_schema

log = logging.getLogger("bgc.llm.batch")

POLL_SECONDS = 20


def batch_structured(client: LLMClient, jobs: list[dict]) -> dict[str, object]:
    """jobs: [{key, purpose, prompt, image, output_model, model?, max_tokens?, page?}]

    Returns {key: parsed_model | Exception}. Cached jobs are answered
    immediately; only the rest are submitted.
    """
    out: dict[str, object] = {}
    to_submit = []
    for job in jobs:
        cached = client.cache_lookup(
            job["purpose"], job["prompt"], job["output_model"],
            model=job.get("model"), image=job.get("image"), page=job.get("page"),
        )
        if cached is not None:
            out[job["key"]] = cached
        else:
            to_submit.append(job)
    if not to_submit:
        return out

    from .ledger import BudgetExceeded, estimate_input_tokens, estimate_request_cost
    from .planner import RecoveryCandidate, select_candidates

    candidates = []
    by_key = {job["key"]: job for job in to_submit}
    for job in to_submit:
        image = job.get("image")
        pixels = int(image.width) * int(image.height) if image is not None else 0
        model = job.get("model") or client.config.llm.repair_model
        candidates.append(RecoveryCandidate(
            key=job["key"],
            kind=job["purpose"],
            page=job.get("page") or 0,
            benefit_units=float(job.get("benefit_units", 1.0)),
            estimated_cost_usd=estimate_request_cost(
                model,
                len(job["prompt"]),
                job.get("max_tokens", 12000),
                image_pixels=pixels,
                batch=True,
            ),
        ))
    plan = select_candidates(
        candidates,
        client.ledger.remaining_cost_usd,
        max_calls=client.ledger.remaining_calls,
    )
    for candidate in plan.skipped:
        out[candidate.key] = BudgetExceeded(
            f"batch budget planner skipped {candidate.key}: "
            f"${candidate.estimated_cost_usd:.4f} worst-case request"
        )

    admitted = [by_key[candidate.key] for candidate in plan.selected]
    reservations = []
    reserved_jobs = []
    try:
        for job in admitted:
            image = job.get("image")
            pixels = int(image.width) * int(image.height) if image is not None else 0
            model = job.get("model") or client.config.llm.repair_model
            try:
                reservation = client.ledger.reserve(
                    model,
                    estimate_input_tokens(len(job["prompt"]), pixels),
                    job.get("max_tokens", 12000),
                    batch=True,
                )
            except BudgetExceeded as exc:
                out[job["key"]] = exc
                continue
            reservations.append(reservation)
            reserved_jobs.append(job)

        if not reserved_jobs:
            return out
        return _submit_reserved_batch(client, reserved_jobs, out)
    finally:
        for reservation in reservations:
            client.ledger.release(reservation)


def _submit_reserved_batch(
    client: LLMClient,
    jobs: list[dict],
    out: dict[str, object],
) -> dict[str, object]:
    api = client._api()
    requests = []
    for index, job in enumerate(jobs):
        model = job.get("model") or client.config.llm.repair_model
        content: list[dict] = []
        if job.get("image") is not None:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(_png_bytes(job["image"])).decode(),
                },
            })
        content.append({"type": "text", "text": job["prompt"]})
        requests.append({
            "custom_id": f"j{index}",
            "params": {
                "model": model,
                "max_tokens": job.get("max_tokens", 12000),
                "messages": [{"role": "user", "content": content}],
                "output_config": {"format": {
                    "type": "json_schema",
                    "schema": _strict_schema(job["output_model"]),
                }},
            },
        })

    batch = api.messages.batches.create(requests=requests)
    log.info("batch %s submitted: %d requests", batch.id, len(requests))
    while True:
        batch = api.messages.batches.retrieve(batch.id)
        log.info("batch %s: %s", batch.id, batch.processing_status)
        if batch.processing_status == "ended":
            break
        time.sleep(POLL_SECONDS)

    by_id = {f"j{index}": job for index, job in enumerate(jobs)}
    for result in api.messages.batches.results(batch.id):
        job = by_id[result.custom_id]
        if result.result.type != "succeeded":
            out[job["key"]] = RuntimeError(f"batch item {result.result.type}")
            continue
        message = result.result.message
        client.ledger.record(
            job["purpose"],
            message.model,
            message.usage.input_tokens,
            message.usage.output_tokens,
            page=job.get("page"),
            batch=True,
        )
        text = "".join(
            block.text
            for block in message.content
            if getattr(block, "type", "") == "text"
        )
        try:
            parsed = job["output_model"].model_validate_json(text)
        except Exception as exc:  # noqa: BLE001 - per-item failure
            out[job["key"]] = exc
            continue
        client.cache_store(
            job["purpose"],
            job["prompt"],
            job["output_model"],
            parsed,
            message.usage.input_tokens,
            message.usage.output_tokens,
            model=job.get("model"),
            image=job.get("image"),
        )
        out[job["key"]] = parsed
    for job in jobs:
        out.setdefault(job["key"], RuntimeError("batch item missing from results"))
    return out
