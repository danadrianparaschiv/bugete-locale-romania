from bgconvertor import nomenclator as nom


def test_normalize_code():
    assert nom.normalize_code("66.02.06.04*") == ("66.02.06.04", "*")
    assert nom.normalize_code("66.02.07 *") == ("66.02.07", "*")
    assert nom.normalize_code(" 04.02.01 ") == ("04.02.01", "")
    assert nom.normalize_code("30.02.08 *)") == ("30.02.08", "*)")
    assert nom.normalize_code("58 06 01") == ("58.06.01", "")
    assert nom.normalize_code("51.0") == ("51", "")


def test_parent_code():
    f, e = "expense_functional", "expense_economic"
    assert nom.parent_code("65.02.04.01", f) == "65.02.04"
    assert nom.parent_code("65.02.04", f) == "65.02"
    assert nom.parent_code("65.02", f) is None
    assert nom.parent_code("65.10", "revenue") is None
    assert nom.parent_code("10.01.01", e) == "10.01"
    assert nom.parent_code("10.01", e) == "10"
    assert nom.parent_code("10", e) is None
    # economic articles ending .10/.02 must NOT be treated as capitole
    assert nom.parent_code("20.10", e) == "20"
    assert nom.parent_code("10.02", e) == "10"


def test_build_registry_from_official_annexes(reference_dir):
    reg = nom.build_registry(reference_dir)
    stats = reg.stats()
    # Sanity floors based on the 2026 annexes (567/172/1048 raw rows incl. headers).
    assert stats["local/revenue"] > 300
    assert stats["local/expense_functional"] > 120
    assert stats["all/expense_economic"] > 700

    # Known codes from the sample PDFs must resolve.
    assert reg.get("revenue", "04.02.01").name.startswith("Cote defalcate")
    assert reg.get("expense_functional", "65.02.04.01") is not None
    assert reg.get("expense_economic", "10.01.01").name == "Salarii de baza"
    # 2026-new code with * marker parses with the marker stripped.
    e = reg.get("expense_functional", "66.02.07")
    assert e is not None and "*" in e.markers

    # Rollup pseudo-codes are known even though they are not in Anexa 2.
    assert reg.exists("00.01")
    assert reg.exists("49.90")
    assert not reg.exists("65.02.99")

    # Hierarchy index.
    assert "65.02.04.01" in reg.children("expense_functional", "65.02.04")


def test_registry_roundtrip(reference_dir, tmp_path):
    reg = nom.build_registry(reference_dir)
    (tmp_path / "x").mkdir()
    path = nom.save_registry(reg, tmp_path / "x")
    loaded = nom.Registry.model_validate_json(path.read_text())
    assert loaded.stats() == reg.stats()
    assert loaded.get("expense_economic", "10.01.01").name == "Salarii de baza"


def test_2024_registry_uses_the_historical_official_annexes(reference_dir):
    historical = reference_dir / "2024"
    reg = nom.build_registry(historical)

    assert reg.effective_year == 2024
    assert len(reg.entries) == 2106
    assert reg.stats() == {
        "local/revenue": 418,
        "local/expense_functional": 141,
        "own_revenue/revenue": 367,
        "own_revenue/expense_functional": 187,
        "all/expense_economic": 993,
        "rollups": 37,
        "identities": 26,
    }
    assert reg.sources == {
        "Anexanr2_05082024.xls": "e64a653a891b4c6a767335365756d40ec7bb91c2f66a791851a1d823e85588ab",
        "Anexanr10_26112024.xls": "1050754db1ba2e688b318330c9e9bf002d68456658d4238772d3158f541f94a4",
        "AnexanrIec_10122024.xls": "c9436d1ffe8ecda0a4fbd3bc28289f4e3362d98c0e4022d161380728a96b456a",
    }

    # Rows annotated with an amendment date and the few space-delimited codes
    # in the MF workbook must remain valid entries.
    assert reg.get("revenue", "04.02.07") is not None
    assert reg.get("expense_economic", "58.06.01") is not None
    assert "extinse" in reg.get("expense_economic", "56.25").name

    # These codes were introduced after the 2024 snapshot.  A 2026 registry
    # would incorrectly accept them in a historical budget.
    assert not reg.exists("54.02.18", "expense_functional")
    assert not reg.exists("66", "expense_economic")


def test_registry_selection_prefers_a_year_snapshot(reference_dir):
    historical = nom.reference_dir_for_year(reference_dir, 2024)
    assert historical == reference_dir / "2024"
    assert nom.load_registry_for_year(reference_dir, 2024).effective_year == 2024
    assert nom.reference_dir_for_year(reference_dir, 2026) == reference_dir


def test_identities_reference_known_codes(reference_dir):
    """Every code an identity references must exist (as entry or rollup)."""
    reg = nom.build_registry(reference_dir)
    for ident in reg.identities:
        for code in [ident.target, *ident.plus, *ident.minus]:
            assert reg.exists(code), f"identity {ident.target}: unknown code {code}"


def test_identity_resuffix():
    from bgconvertor.rules import Identity

    ident = Identity(target="00.06", plus=["03.02", "04.02"], scope="revenue")
    r = ident.resuffix("10")
    assert r.plus == ["03.10", "04.10"]
    assert ident.plus == ["03.02", "04.02"]  # original untouched
