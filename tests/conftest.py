from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def reference_dir() -> Path:
    d = PROJECT_ROOT / "reference" / "nomenclator"
    if not any(d.glob("Anexanr2_*.xlsx")):
        pytest.skip("nomenclator annexes not present")
    return d


@pytest.fixture
def ab_pdf() -> Path:
    p = PROJECT_ROOT / "budget_file_ab.pdf"
    if not p.exists():
        pytest.skip("sample PDF not present")
    return p
