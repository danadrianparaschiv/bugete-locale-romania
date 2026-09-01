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
vision_draft = _load_script("annotation_vision_draft")


def _fact(page: int, row: int, value: str) -> consensus.Fact:
    return consensus.Fact(
        page=page,
        row=row,
        identity="code:04.02",
        column="total_2024",
        value=value,
    )


def test_vision_draft_bands_cover_source_once_and_avoid_dark_target_row():
    from PIL import Image, ImageDraw

    image = Image.new("L", (200, 200), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 98, 190, 102), fill="black")

    bands = vision_draft._split_image_bands(image, 2)

    assert len(bands) == 2
    assert bands[0][0] == 0
    assert bands[0][1] == bands[1][0]
    assert bands[1][1] == 1
    assert bands[0][2].height + bands[1][2].height == image.height
    assert not 0.49 <= bands[0][1] <= 0.515


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


def test_coordinate_consensus_exposes_numeric_cells_missing_from_draft():
    report = ocr_consensus._compare_page(
        [{"trim1": "25"}],
        [{
            "values": {"total_2024": "100", "trim1": "25"},
            "cells": {
                "total_2024": {
                    "printed": "100", "confidence": 0.99, "box": []
                },
                "trim1": {"printed": "25", "confidence": 0.99, "box": []},
            },
        }],
        1,
    )

    assert report["extra_observations"] == [{
        "page": 1,
        "draft_row": 1,
        "ocr_row": 1,
        "column": "total_2024",
        "ocr_value": "100",
        "ocr_printed": "100",
        "ocr_confidence": 0.99,
        "ocr_box": [],
    }]


def test_coordinate_consensus_reports_zero_confirmations_explicitly():
    report = ocr_consensus._compare_page(
        [{"total_2024": "100"}],
        [{
            "values": {"total_2024": "999"},
            "cells": {
                "total_2024": {"printed": "999", "confidence": 1.0, "box": []}
            },
        }],
        1,
    )

    assert report["confirmed"] == 0
    assert report["conflicting"] == 0
    assert report["uncovered"] == 1


def test_coordinate_ocr_separates_adjacent_x_marker_from_numeric_total():
    assert ocr_consensus._canonical("250 x") == "250"
    assert ocr_consensus._canonical("X") is None


@pytest.mark.parametrize(
    ("printed", "canonical"),
    [
        ("TOTAL 2024", "total_2024"),
        ("TRIM IV", "trim4"),
        ("2026", "est2026"),
        (
            "din care credite bugetare destinate stingerii platilor restante",
            "credite_restante",
        ),
        ("est2027consolidated", "est2027"),
    ],
)
def test_vision_header_aliases_are_canonicalized(printed, canonical):
    assert ocr_consensus._canonical_column(printed) == canonical
    assert importer._canonical_column(printed) == canonical


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


def test_independent_drafts_align_rows_and_confirm_only_exact_cells(tmp_path):
    primary = {
        1: {"reading": {"rows": [
            {"name": "Target", "cells": [
                {"column": "total_2024", "value": "100"}
            ]},
            {"name": "Other", "cells": [
                {"column": "total_2024", "value": "200"}
            ]},
        ]}}
    }
    directory = tmp_path / "second"
    (directory / "pages").mkdir(parents=True)
    (directory / "pages" / "p0001.json").write_text(json.dumps({
        "source_sha256": "abc",
        "source_page": 1,
        "reading": {"rows": [
            {"name": "Noise", "cells": [
                {"column": "TOTAL 2024", "value": "999"}
            ]},
            {"name": "Target", "cells": [
                {"column": "TOTAL 2024", "value": "100"}
            ]},
            {"name": "Other", "cells": [
                {"column": "TOTAL 2024", "value": "201"},
                {"column": "trim1", "value": "not-a-number"},
            ]},
        ]},
    }))

    assert importer._independent_draft_evidence(directory, primary, "abc") == {
        (1, 1, "total_2024", "100")
    }


def test_import_requires_named_visual_reviewer(tmp_path):
    path = tmp_path / "visual.json"
    path.write_text(json.dumps({"confirmed": []}))

    with pytest.raises(ValueError, match="reviewer"):
        importer._visual_evidence(path)


def test_bulk_visual_correction_moves_cells_and_counts_as_evidence():
    pages = {1: {"reading": {"rows": [{"cells": [
        {"column": "TOTAL 2024", "value": "0"}
    ]}]}}}
    payload = {
        "corrections": [{
            "page": 1,
            "rows": "1",
            "from": {"column": "TOTAL 2024", "value": "0"},
            "to": {"column": "credite_restante", "value": "0"},
        }]
    }

    corrections = importer._correction_map(payload, pages)

    assert importer._draft_cells(pages, corrections) == {
        (1, 1, "credite_restante", "0")
    }
    assert set(corrections.values()) == {(1, 1, "credite_restante", "0")}


def test_review_edits_can_replace_add_and_delete_rows():
    pages = {1: {"reading": {"rows": [
        {"cells": [{"column": "total_2024", "value": "10"}]},
        {"cells": [{"column": "total_2024", "value": "99"}]},
    ]}}}
    payload = {
        "row_replacements": [{
            "page": 1, "row": 1, "values": {"total_2024": "5", "trim1": "5"}
        }],
        "additions": [{"page": 1, "row": 1, "column": "trim2", "value": "0"}],
        "deleted_rows": [{"page": 1, "rows": "2"}],
    }
    overrides = importer._row_overrides(payload, pages)
    additions = importer._additions(payload, pages)
    deleted = importer._deleted_rows(payload, pages)

    assert importer._draft_cells(
        pages, row_overrides=overrides, additions=additions, deleted_rows=deleted
    ) == {
        (1, 1, "total_2024", "5"),
        (1, 1, "trim1", "5"),
        (1, 1, "trim2", "0"),
    }


def test_classification_ranges_must_cover_every_source_page(tmp_path):
    path = tmp_path / "classifications.json"
    path.write_text(json.dumps({
        "source_sha256": "abc",
        "reviewer": "reviewer-a",
        "classifications": [
            {"pages": "1-2", "page_kind": "not_relevant"},
            {"pages": "3-4", "page_kind": "budget_table"},
        ],
    }))

    reviewer, decisions = importer._classification_decisions(path, "abc", 4)

    assert reviewer == "reviewer-a"
    assert decisions[3]["page_kind"] == "budget_table"


def test_import_excludes_non_numeric_x_markers_from_ground_truth():
    assert importer._canonical("X") is None
    assert importer._canonical("—") is None
    assert importer._canonical("0") == "0"
    assert importer._canonical("0X") == "0"
    assert importer._canonical("8.819") == "8819"
    assert importer._canonical("1,5") == "1.5"


def test_review_payload_does_not_invent_context_for_identical_rows():
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

    assert all("section" not in row for row in payload["rows"])


def test_review_payload_does_not_invent_context_for_different_values():
    draft = {
        "columns": ["total_2024"],
        "reading": {"rows": [
            {"code": "61.00/.20", "name": "Bunuri", "cells": [
                {"column": "total_2024", "value": "100"}
            ]},
            {"code": "61.00/.20", "name": "Bunuri", "cells": [
                {"column": "total_2024", "value": "60"}
            ]},
        ]},
    }
    evidence = {
        (1, 1, "total_2024", "100"): "OCR local",
        (1, 2, "total_2024", "60"): "OCR local",
    }

    payload = importer._review_payload(
        1, draft, evidence, revision=0, reviewer="reviewer-a"
    )

    assert all("section" not in row for row in payload["rows"])


def test_review_payload_preserves_institution_context_for_repeated_codes():
    draft = {
        "columns": ["total_2024"],
        "reading": {"rows": [
            {"code": "65.00.20", "name": "Bunuri", "section": "Școala A",
             "cells": [{"column": "total_2024", "value": "100"}]},
            {"code": "65.00.20", "name": "Bunuri", "section": "Școala B",
             "cells": [{"column": "total_2024", "value": "60"}]},
        ]},
    }
    evidence = {
        (1, 1, "total_2024", "100"): "OCR local",
        (1, 2, "total_2024", "60"): "OCR local",
    }

    payload = importer._review_payload(
        1, draft, evidence, revision=0, reviewer="reviewer-a"
    )

    assert [row["institution"] for row in payload["rows"]] == ["Școala A", "Școala B"]


def test_review_payload_recognizes_section_context_with_romanian_diacritics():
    draft = {
        "columns": ["total_2024"],
        "reading": {"rows": [{
            "code": "00.01",
            "name": "Venituri",
            "section": "VENITURILE SECŢIUNII DE FUNCŢIONARE",
            "cells": [{"column": "total_2024", "value": "100"}],
        }]},
    }
    evidence = {(1, 1, "total_2024", "100"): "OCR local"}

    payload = importer._review_payload(
        1, draft, evidence, revision=0, reviewer="reviewer-a"
    )

    assert payload["rows"][0]["section"] == "VENITURILE SECŢIUNII DE FUNCŢIONARE"
    assert "institution" not in payload["rows"][0]


def test_review_payload_uses_reviewed_hierarchy_for_many_legitimate_repetitions():
    draft = {
        "columns": ["total_2024"],
        "reading": {"rows": [
            {"code": None, "name": "Cheltuieli de personal", "cells": [
                {"column": "total_2024", "value": str(value)}
            ]}
            for value in (100, 200, 300, 400)
        ]},
    }
    evidence = {
        (1, index, "total_2024", str(value)): "review vizual"
        for index, value in enumerate((100, 200, 300, 400), 1)
    }
    contexts = {
        (1, index): {"subdocument": name}
        for index, name in enumerate(("Cămin", "Invaliditate", "Minori", "Adulți"), 1)
    }

    payload = importer._review_payload(
        1,
        draft,
        evidence,
        revision=0,
        reviewer="reviewer-a",
        row_contexts=contexts,
    )

    assert [row["subdocument"] for row in payload["rows"]] == [
        "Cămin", "Invaliditate", "Minori", "Adulți"
    ]


def test_row_contexts_expands_ranges_and_rejects_overlaps():
    pages = {1: {"reading": {"rows": [{}, {}, {}]}}}
    payload = {"row_contexts": [
        {"page": 1, "rows": "1-2", "subdocument": "Cămin"},
    ]}

    assert importer._row_contexts(payload, pages) == {
        (1, 1): {"subdocument": "Cămin"},
        (1, 2): {"subdocument": "Cămin"},
    }

    payload["row_contexts"].append(
        {"page": 1, "rows": "2-3", "subdocument": "Centru"}
    )
    with pytest.raises(ValueError, match="context de rând duplicat"):
        importer._row_contexts(payload, pages)


def test_row_contexts_can_explicitly_clear_a_machine_draft_context():
    pages = {1: {"reading": {"rows": [{"section": "Context greșit"}]}}}
    contexts = importer._row_contexts({
        "row_contexts": [{"page": 1, "rows": "1", "clear": ["institution"]}],
    }, pages)
    evidence = {(1, 1, "total_2024", "10"): "review vizual"}
    draft = {"columns": ["total_2024"], "reading": {"rows": [{
        "name": "Rând",
        "section": "Context greșit",
        "cells": [{"column": "total_2024", "value": "10"}],
    }]}}

    review = importer._review_payload(
        1, draft, evidence, revision=0, reviewer="reviewer-a",
        row_contexts=contexts,
    )

    assert review["rows"][0]["institution"] is None


def test_vision_inventory_mode_does_not_require_transcription_columns():
    parser = vision_draft.build_parser()

    args = parser.parse_args([
        "source.pdf", "draft", "--year", "2024", "--inventory-only"
    ])

    assert args.inventory_only is True
    assert args.columns is None
    reading = vision_draft.InventoryReading(
        page_kind="budget_table",
        orientation=90,
        source_unit="mii_lei",
        table_family="buget anual",
        columns=["TOTAL 2024", "TRIM I"],
    )
    assert reading.orientation == 90
