import csv
from decimal import Decimal
from pathlib import Path

from bgconvertor import crossyear

HEADER = ["municipality", "siruta", "year", "suffix", "section", "kind",
          "code", "func_code", "column", "value", "verified", "page"]


def _write(path: Path, rows):
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerows(rows)


def _row(code, value, verified="True", page=1, col="total"):
    return ["Testopolis", "1017", "2026", "02", "TOTAL", "expense_economic",
            code, "51.02", col, value, verified, page]


def _pair(tmp_path, old_vals, new_vals, verified_new="True"):
    old = tmp_path / "old.csv"
    new = tmp_path / "new.csv"
    _write(old, [_row(f"20.01.{i:02d}", v) for i, v in enumerate(old_vals)])
    _write(new, [_row(f"20.01.{i:02d}", v, verified=verified_new)
                 for i, v in enumerate(new_vals)])
    return old, new


def test_normal_growth_produces_no_suspects(tmp_path):
    # 25 de linii care cresc toate cu ~10% — creștere reală de buget
    old = [100 + i for i in range(25)]
    new = [round((100 + i) * 1.1, 2) for i in range(25)]
    reports = crossyear.compare(*_pair(tmp_path, old, new))
    assert reports and reports[0].suspects == []
    assert 1.05 < reports[0].median_ratio < 1.15


def test_decimal_shift_is_flagged_and_prioritized(tmp_path):
    old = [100 + i for i in range(25)]
    new = [round((100 + i) * 1.1, 2) for i in range(25)]
    new[7] = round(old[7] * 1.1 * 1000, 2)  # separator de mii pierdut
    reports = crossyear.compare(*_pair(tmp_path, old, new, verified_new="False"))
    s = reports[0].suspects
    assert len(s) == 1
    assert s[0].signature == "decimal_shift"
    assert s[0].code == "20.01.07"
    assert s[0].old_verified and not s[0].new_verified


def test_unit_change_reported_once_not_per_line(tmp_path):
    # toată ediția nouă e în lei în loc de mii lei
    old = [100 + i for i in range(25)]
    new = [(100 + i) * 1000 for i in range(25)]
    rep = crossyear.compare(*_pair(tmp_path, old, new))[0]
    assert rep.unit_shift == 1000
    assert rep.suspects == []  # mediana absoarbe schimbarea de unitate


def test_too_little_overlap_is_skipped(tmp_path):
    reports = crossyear.compare(*_pair(tmp_path, [100] * 5, [110] * 5))
    assert reports == []


def test_verified_pair_ranks_below_unverified(tmp_path):
    old = [100 + i for i in range(25)]
    new = [round((100 + i) * 1.1, 2) for i in range(25)]
    new[3] = round(old[3] * 1.1 * 100, 2)
    both_ok = crossyear.compare(*_pair(tmp_path, old, new, verified_new="True"))[0]
    new_bad = crossyear.compare(*_pair(tmp_path, old, new, verified_new="False"))[0]
    assert new_bad.suspects[0].priority > both_ok.suspects[0].priority


def test_nothing_is_mutated(tmp_path):
    old, new = _pair(tmp_path, [100 + i for i in range(25)],
                     [200 + i * 9 for i in range(25)])
    before = new.read_text()
    crossyear.compare(old, new)
    assert new.read_text() == before  # raportul nu atinge datele


def test_write_csv_roundtrip(tmp_path):
    old = [100 + i for i in range(25)]
    new = [round((100 + i) * 1.1, 2) for i in range(25)]
    new[5] = round(old[5] * 1.1 * 1000, 2)
    reports = crossyear.compare(*_pair(tmp_path, old, new, verified_new="False"))
    out = tmp_path / "cc.csv"
    assert crossyear.write_csv(reports, out) == 1
    row = next(iter(csv.DictReader(out.open())))
    assert row["semnatura"] == "decimal_shift"
    assert Decimal(row["valoare_noua"]) > Decimal(row["valoare_veche"])
