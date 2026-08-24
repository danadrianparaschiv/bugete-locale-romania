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
    assert report["metric"] == "selected_anchor_recall"
    assert report["full_cell_recall_measured"] is False
    assert report["fixtures"] == {"total": 1, "evaluated": 1, "missing": 0}
    assert report["anchors"] == {"matched": 1, "total": 1, "pct": 100.0}
