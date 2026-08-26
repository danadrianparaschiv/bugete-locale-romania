from bgconvertor.llm.planner import RecoveryCandidate, select_candidates


def _candidate(key, benefit, cost, *, cached=False, page=1):
    return RecoveryCandidate(
        key=key,
        kind="repair",
        page=page,
        benefit_units=benefit,
        estimated_cost_usd=cost,
        cached=cached,
    )


def test_planner_selects_cached_then_highest_expected_gain_per_dollar():
    plan = select_candidates(
        [
            _candidate("large", 20, 1.0),
            _candidate("efficient", 8, 0.2),
            _candidate("medium", 10, 0.5),
            _candidate("cached", 1, 99.0, cached=True),
        ],
        budget_usd=0.7,
    )

    assert [candidate.key for candidate in plan.selected] == [
        "cached", "efficient", "medium",
    ]
    assert [candidate.key for candidate in plan.skipped] == ["large"]
    assert plan.estimated_cost_usd == 0.7
    assert plan.expected_benefit_units == 19
    assert plan.as_dict()["policy"].startswith("cached_then")


def test_planner_call_limit_counts_only_paid_candidates_and_is_stable():
    plan = select_candidates(
        [
            _candidate("later-page", 5, 0.1, page=2),
            _candidate("first-page", 5, 0.1, page=1),
            _candidate("free", 1, 1.0, cached=True),
        ],
        budget_usd=1.0,
        max_calls=1,
    )

    assert [candidate.key for candidate in plan.selected] == ["free", "first-page"]
    assert [candidate.key for candidate in plan.skipped] == ["later-page"]


def test_planner_reserves_both_calls_for_possible_premium_escalation():
    candidate = RecoveryCandidate(
        key="tiered",
        kind="repair",
        page=1,
        benefit_units=10,
        estimated_cost_usd=0.5,
        estimated_calls=2,
    )

    blocked = select_candidates([candidate], budget_usd=1, max_calls=1)
    admitted = select_candidates([candidate], budget_usd=1, max_calls=2)

    assert not blocked.selected
    assert admitted.selected == (candidate,)
    assert admitted.as_dict()["selected"][0]["estimated_calls"] == 2
