import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

import pytest


def _load_script(name: str):
    path = Path(__file__).parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"test_scripts.{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


consensus = _load_script("annotation_consensus")
importer = _load_script("annotation_import_draft")
ocr_consensus = _load_script("annotation_ocr_consensus")


def _fact(page: int, row: int, value: str) -> consensus.Fact:
    return consensus.Fact(
        page=page,
        row=row,
        identity="code:04.02",
        column="total_2024",
        value=value,
    )


def test_component_consensus_confirms_sum_without_mutating_source_counters():
    functionare = Counter({"60": 1})
    dezvoltare = Counter({"40": 1})

    assert consensus._component_match("100", functionare, dezvoltare) == ("60", "40")
    assert functionare == Counter({"60": 0})
    assert dezvoltare == Counter({"40": 0})


def test_consensus_reports_each_primary_fact_and_complete_denominator():
    report = consensus.compare(
        {1: [_fact(1, 1, "100"), _fact(1, 2, "7")]},
        {
            "functionare": {1: [_fact(1, 1, "60")]},
            "dezvoltare": {1: [_fact(1, 1, "40")]},
        },
    )

    assert report["totals"] == {
        "primary_facts": 2,
        "confirmed": 1,
        "sum_confirmed": 1,
        "single_source_confirmed": 0,
        "conflicting": 0,
        "uncovered": 1,
    }
    assert [fact["status"] for fact in report["pages"][0]["facts"]] == [
        "sum_confirmed",
        "uncovered",
    ]


def test_coordinate_consensus_aligns_rows_monotonically_and_counts_conflict():
    draft = [
        {"total_2024": "100", "trim1": "25"},
        {"total_2024": "200", "trim1": "50"},
    ]
    ocr = [
        {"values": {"total_2024": "999"}, "cells": {}},
        {
            "values": {"total_2024": "100", "trim1": "25"},
            "cells": {
                "total_2024": {"printed": "100", "confidence": 0.9, "box": []},
                "trim1": {"printed": "25", "confidence": 0.9, "box": []},
            },
        },
        {
            "values": {"total_2024": "201", "trim1": "50"},
            "cells": {
                "total_2024": {"printed": "201", "confidence": 0.8, "box": []},
                "trim1": {"printed": "50", "confidence": 0.9, "box": []},
            },
        },
    ]

    report = ocr_consensus._compare_page(draft, ocr, 3)

    assert report["aligned_rows"] == 2
    assert report["expected"] == 4
    assert report["confirmed"] == 3
    assert report["conflicting"] == 1
    assert report["discrepancies"][0]["kind"] == "conflict"


def test_import_evidence_never_treats_reported_disagreement_as_confirmation(tmp_path):
    report = tmp_path / "ocr.json"
    report.write_text(json.dumps({
        "pages": [{
            "page": 1,
            "discrepancies": [{
                "page": 1,
                "draft_row": 2,
                "column": "trim1",
                "draft_value": "25",
            }],
        }],
    }))
    cells = {
        (1, 1, "total_2024", "100"),
        (1, 2, "trim1", "25"),
        (2, 1, "total_2024", "300"),
    }

    assert importer._ocr_evidence(report, cells) == {(1, 1, "total_2024", "100")}


def test_import_requires_named_visual_reviewer(tmp_path):
    path = tmp_path / "visual.json"
    path.write_text(json.dumps({"confirmed": []}))

    with pytest.raises(ValueError, match="reviewer"):
        importer._visual_evidence(path)


def test_review_payload_disambiguates_legitimate_identical_rows():
    draft = {
        "columns": ["total_2024"],
        "reading": {
            "rows": [
                {"code": "04.02", "name": "Cote", "cells": [
                    {"column": "total_2024", "value": "100"}
                ]},
                {"code": "04.02", "name": "Cote", "cells": [
                    {"column": "total_2024", "value": "100"}
                ]},
            ]
        },
    }
    evidence = {
        (1, 1, "total_2024", "100"): "componente oficiale",
        (1, 2, "total_2024", "100"): "review vizual",
    }

    payload = importer._review_payload(
        1, draft, evidence, revision=0, reviewer="reviewer-a"
    )

    assert [row["section"] for row in payload["rows"]] == ["total", "functionare"]
