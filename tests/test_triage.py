from pathlib import Path

from bgconvertor.triage import _recommend


def test_recommended_recovery_command_never_exceeds_public_file_cap():
    command = _recommend(Path("large-scan.pdf"), n_scanned=200, est_cost=18.0)
    assert "--workers 4" in command
    assert "--max-llm-cost 5.00" in command


def test_recommended_recovery_command_keeps_small_estimate():
    command = _recommend(Path("small-scan.pdf"), n_scanned=4, est_cost=1.2)
    assert "--workers" not in command
    assert "--max-llm-cost 2.00" in command
