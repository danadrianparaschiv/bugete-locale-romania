import json

from bgconvertor.manifest import Manifest


def _write_manifest(tmp_path):
    data = {
        "year": 2026,
        "entries": [
            {
                "county_code": "01",
                "county_name": "Alba",
                "capital_siruta": "1017",
                "capital_name": "Alba Iulia",
                "path": "01-alba/1017-alba-iulia/budget_file.pdf",
            }
        ],
    }
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return p


def test_set_status_preserves_fields_added_after_load(tmp_path):
    """A long-lived process (batch) must not clobber fields another process
    wrote to the manifest since it loaded — set_status merges onto disk."""
    p = _write_manifest(tmp_path)
    stale = Manifest(p)  # loaded before the other writer runs
    city = stale.cities()[0]

    other = Manifest(p)
    other.data["entries"][0]["timeline"] = {"approved_date": "2026-05-05"}
    other.save()

    stale.set_status(city, status="converted", lines=42)

    on_disk = json.loads(p.read_text())["entries"][0]
    assert on_disk["timeline"] == {"approved_date": "2026-05-05"}
    assert on_disk["conversion"]["status"] == "converted"
    assert on_disk["conversion"]["lines"] == 42


def test_set_status_successive_updates(tmp_path):
    p = _write_manifest(tmp_path)
    m = Manifest(p)
    city = m.cities()[0]
    m.set_status(city, status="converting")
    m.set_status(city, status="converted", pct_clean=99.5)
    conv = json.loads(p.read_text())["entries"][0]["conversion"]
    assert conv == {"status": "converted", "pct_clean": 99.5}
