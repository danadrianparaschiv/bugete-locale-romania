import json
from pathlib import Path

from bgconvertor import eval_harness as ev
from bgconvertor.config import RunConfig
from bgconvertor.runstore import RunStore

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "golden"


def test_all_committed_fixtures_are_valid():
    fixtures = ev.load_fixtures(FIXTURES_DIR)
    assert len(fixtures) >= 12
    layouts = {f.layout for f in fixtures}
    # every layout family from the corpus analysis is represented
    for family in [
        "digital_detail",
        "hcl_prose",
        "scan_simple_table",
        "scan_detail_economic",
        "scan_general_matrix",
        "scan_revenue_detail",
        "scan_expense_chapter",
        "scan_institution_budget",
        "investment_list",
    ]:
        assert family in layouts, f"no fixture for layout {family}"
    # every fixture asserts something
    for f in fixtures:
        assert f.anchors or f.text_contains, f.id
    alba_digital = next(f for f in fixtures if f.id == "ab_p001")
    assert sum(
        len(row.values)
        for group in alba_digital.cell_ground_truth
        for row in group.rows
    ) == 180
    arad_matrix = next(f for f in fixtures if f.id == "ar_p001")
    assert sum(
        len(row.values)
        for group in arad_matrix.cell_ground_truth
        for row in group.rows
    ) == 232
    assert arad_matrix.source_grid
    bistrita_transposed = next(f for f in fixtures if f.id == "bn_p002")
    assert sum(
        len(row.values)
        for group in bistrita_transposed.cell_ground_truth
        for row in group.rows
    ) == 216
    assert bistrita_transposed.source_grid
    institution = next(f for f in fixtures if f.id == "ar_p301")
    assert sum(len(group.cells) for group in institution.cell_ground_truth) == 51
    assert institution.source_grid
    pitesti = next(f for f in fixtures if f.id == "ag_p009")
    assert sum(
        len(row.values)
        for group in pitesti.cell_ground_truth
        for row in group.rows
    ) == 216
    assert pitesti.source_grid
    pitesti_detail = next(f for f in fixtures if f.id == "ag_p041")
    assert sum(
        len(row.values)
        for group in pitesti_detail.cell_ground_truth
        for row in group.rows
    ) == 245
    assert pitesti_detail.source_grid
    arad_expense = next(f for f in fixtures if f.id == "ar_p151")
    assert sum(
        len(row.values)
        for group in arad_expense.cell_ground_truth
        for row in group.rows
    ) == 80
    assert arad_expense.source_grid
    pitesti_investment = next(f for f in fixtures if f.id == "ag_p171")
    assert sum(
        len(row.values)
        for group in pitesti_investment.cell_ground_truth
        for row in group.rows
    ) == 62
    assert pitesti_investment.source_grid
    arad_revenue = next(f for f in fixtures if f.id == "ar_p031")
    assert sum(
        len(row.values)
        for group in arad_revenue.cell_ground_truth
        for row in group.rows
    ) == 73
    assert sum(anchor.value == "X" for anchor in arad_revenue.anchors) == 15
    assert arad_revenue.source_grid
    recovery_samples = {
        fixture.id: fixture for fixture in fixtures
        if fixture.id in {"bn24_p030", "br24_p167", "hd24_p226", "sj24_p024"}
    }
    assert set(recovery_samples) == {
        "bn24_p030", "br24_p167", "hd24_p226", "sj24_p024"
    }
    assert all(fixture.source_grid for fixture in recovery_samples.values())
    # hazard coverage for the hard cases
    all_hazards = {h for f in fixtures for h in f.hazards}
    assert "rotated_90_in_image" in all_hazards
    assert "stamp_overlap" in all_hazards


def _payload():
    return {
        "lines": [
            {
                "raw_code": "610203",
                "code": "61.02.03",
                "name": "Ordine publica (cod 61.02.03.04)",
                "section": None,
                "values": {"total_2026": "21303", "est2027": "21303"},
            },
            {
                "raw_code": "5940",
                "code": "59.40",
                "name": "Sume aferente persoanelor cu handicap neincadrate",
                "section": "68020502 Asistenta sociala in caz de invaliditate",
                "values": {"total": "4.00"},
            },
            {
                "raw_code": None,
                "code": None,
                "name": "VENITURI TOTAL",
                "year": 2026,
                "values": {"total": "1439793.50"},
            },
        ],
        "text": "HOTARARE privind aprobarea bugetului",
    }


def test_anchor_matching_by_raw_code():
    a = ev.Anchor(raw_code="610203", column="total_2026", value="21303")
    assert ev.check_anchor(_payload(), a).matched
    # decimal-equal comparison: 21303 == 21303.00
    a2 = ev.Anchor(raw_code="610203", column="total_2026", value="21303.00")
    assert ev.check_anchor(_payload(), a2).matched
    a3 = ev.Anchor(raw_code="610203", column="est2028", value="21303")
    assert not ev.check_anchor(_payload(), a3).matched


def test_anchor_context_disambiguation():
    hit = ev.Anchor(raw_code="5940", context_contains="invaliditate", column="total", value="4.00")
    assert ev.check_anchor(_payload(), hit).matched
    miss = ev.Anchor(raw_code="5940", context_contains="alta sectiune", column="total", value="4.00")
    assert not ev.check_anchor(_payload(), miss).matched


def test_anchor_diacritic_folding():
    a = ev.Anchor(name_contains="Sume aferente persoanelor cu handicap neîncadrate",
                  column="total", value="4.00")
    assert ev.check_anchor(_payload(), a).matched


def test_anchor_year_matching():
    a = ev.Anchor(name_contains="VENITURI TOTAL", row_year=2026, column="total", value="1439793.50")
    assert ev.check_anchor(_payload(), a).matched
    a27 = ev.Anchor(name_contains="VENITURI TOTAL", row_year=2027, column="total", value="1")
    assert not ev.check_anchor(_payload(), a27).matched


def test_evaluate_fixture_missing_and_text():
    fx = ev.Fixture(
        id="t", pdf="x.pdf", page=1, layout="hcl_prose", source_type="scanned",
        text_contains=["privind aprobarea bugetului", "nu exista"],
    )
    missing = ev.evaluate_fixture(fx, None)
    assert missing.status == "missing"
    r = ev.evaluate_fixture(fx, _payload())
    assert (r.text_matched, r.text_total) == (1, 2)
    assert any("nu exista" in m for m in r.misses)


def test_evaluate_all_against_run_store(tmp_path):
    """End-to-end: a synthetic extraction in a run store scores via evaluate_all."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    config = RunConfig(runs_dir=tmp_path / "runs")
    store = RunStore(config, pdf)
    store.put("extract", 3, _payload())

    fdir = tmp_path / "golden"
    fdir.mkdir()
    fx = ev.Fixture(
        id="doc_p003", pdf="doc.pdf", page=3, layout="scan_simple_table",
        source_type="scanned",
        anchors=[ev.Anchor(raw_code="610203", column="total_2026", value="21303")],
    )
    (fdir / "doc_p003.json").write_text(fx.model_dump_json())

    results = ev.evaluate_all(config, fdir, tmp_path, stage="extract")
    assert results[0].status == "evaluated"
    assert results[0].anchors_matched == 1

    summary = ev.summarize_by_layout(results)
    assert summary["scan_simple_table"]["anchors_matched"] == 1

    report = ev.evaluation_report(results)
    assert report["schema_version"] == 2
    assert report["metric"] == "selected_anchor_recall"
    assert report["full_cell_recall_measured"] is False
    assert report["fixtures"] == {"total": 1, "evaluated": 1, "missing": 0}
    assert report["anchors"] == {"matched": 1, "total": 1, "pct": 100.0}
    assert report["validated_cell_recall"]["pct"] is None


def test_committed_source_grid_overrides_local_run_cache(tmp_path, monkeypatch):
    """Offline golden scores cannot depend on whichever PDF ran most recently."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF fake")
    config = RunConfig(runs_dir=tmp_path / "runs")
    store = RunStore(config, pdf)
    store.put("extract", 3, {"lines": []})

    fdir = tmp_path / "golden"
    fdir.mkdir()
    fx = ev.Fixture(
        id="doc_p003",
        pdf="doc.pdf",
        page=3,
        layout="scan_simple_table",
        source_type="scanned",
        source_grid="grids/doc_p003.json",
        anchors=[
            ev.Anchor(raw_code="610203", column="total_2026", value="21303")
        ],
    )
    (fdir / "doc_p003.json").write_text(fx.model_dump_json())
    monkeypatch.setattr(ev, "_source_grid_payload", lambda *_: _payload())

    result = ev.evaluate_all(config, fdir, tmp_path, stage="extract")[0]
    assert result.status == "evaluated"
    assert result.anchors_matched == 1


def test_exhaustive_cells_measure_recall_precision_and_duplicates_once():
    payload = {
        "lines": [
            {
                "raw_code": "20.01", "code": "20.01", "name": "Bunuri",
                "section": "Scoala A", "values": {"total": "10.00"},
            },
            {
                "raw_code": "20.02", "code": "20.02", "name": "Reparatii",
                "section": "Scoala A", "values": {"total": "99.00"},
            },
        ]
    }
    group = ev.CellGroundTruthGroup(
        context_contains="Scoala A",
        cells=[
            ev.Anchor(raw_code="20.01", column="total", value="10.00"),
            ev.Anchor(raw_code="20.02", column="total", value="20.00"),
        ],
    )
    matched, expected, predicted, misses = ev.check_cell_ground_truth(payload, [group])
    assert (matched, expected, predicted) == (1, 2, 2)
    assert any("cell missing" in miss for miss in misses)
    assert any("unexpected cell" in miss for miss in misses)

    duplicate = ev.CellGroundTruthGroup(
        context_contains="Scoala A",
        cells=[
            ev.Anchor(raw_code="20.01", column="total", value="10.00"),
            ev.Anchor(raw_code="20.01", column="total", value="10.00"),
        ],
    )
    assert ev.check_cell_ground_truth(payload, [duplicate])[:3] == (1, 2, 2)

    empty_result = ev.evaluate_fixture(
        ev.Fixture(
            id="empty", pdf="x.pdf", page=1, layout="scan_simple_table",
            source_type="scanned", cell_ground_truth=[group],
        ),
        {"lines": []},
    )
    report = ev.evaluation_report([empty_result])
    assert report["validated_cell_recall"]["pct"] == 0.0
    assert report["numeric_cell_precision_against_ground_truth"]["pct"] == 0.0


def test_exhaustive_compact_rows_can_scope_the_whole_payload():
    payload = {
        "lines": [
            {
                "raw_code": "61.02", "code": "61.02", "name": "Ordine",
                "section": None,
                "values": {"total_2026": "10", "est2027": "11"},
            },
            {
                "raw_code": "65.02", "code": "65.02", "name": "Invatamant",
                "section": None,
                "values": {"total_2026": "20", "est2027": "21"},
            },
        ]
    }
    group = ev.CellGroundTruthGroup(
        rows=[
            ev.CellGroundTruthRow(
                raw_code="61.02", values={"total_2026": "10", "est2027": "11"}
            ),
            ev.CellGroundTruthRow(
                raw_code="65.02", values={"total_2026": "20", "est2027": "21"}
            ),
        ]
    )

    assert ev.check_cell_ground_truth(payload, [group]) == (4, 4, 4, [])


def test_evaluate_all_uses_committed_source_grid_without_pdf(tmp_path):
    fdir = tmp_path / "golden"
    grids = fdir / "grids"
    grids.mkdir(parents=True)
    source = {
        "grid": [
            ["DENUMIREA INDICATORILOR", "Cod indicator", "Buget 2026"],
            ["Ordine publica", "610203", "21.303,00"],
        ]
    }
    (grids / "doc.json").write_text(json.dumps(source))
    fixture = ev.Fixture(
        id="doc_p001", pdf="missing.pdf", page=1,
        layout="scan_simple_table", source_type="scanned",
        source_grid="grids/doc.json",
        anchors=[ev.Anchor(raw_code="610203", column="total_2026", value="21303")],
    )
    (fdir / "doc_p001.json").write_text(fixture.model_dump_json())

    results = ev.evaluate_all(RunConfig(runs_dir=tmp_path / "runs"), fdir, tmp_path)
    assert results[0].status == "evaluated"
    assert results[0].anchors_matched == 1
