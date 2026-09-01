from bgconvertor.extract.orient import AdaptiveOrient


def test_close_rotated_score_uses_document_block_continuity():
    detector = AdaptiveOrient()
    first = detector._remember({
        "rotation": 270,
        "scores": {"270": 946.0, "180": 718.0},
    })
    ambiguous = detector._remember({
        "rotation": 180,
        "scores": {"270": 611.0, "180": 666.0},
    })

    assert first["rotation"] == 270
    assert ambiguous["rotation"] == 270
    assert ambiguous["continuity_override"] is True


def test_decisive_rotation_change_and_upright_reset_are_preserved():
    detector = AdaptiveOrient()
    detector._remember({"rotation": 270, "scores": {"270": 900.0}})
    changed = detector._remember({
        "rotation": 180,
        "scores": {"270": 400.0, "180": 800.0},
    })
    assert changed["rotation"] == 180

    for _ in range(3):
        detector._remember({"rotation": 0, "scores": {"0": 1500.0}})
    after_reset = detector._remember({
        "rotation": 270,
        "scores": {"270": 510.0, "180": 500.0},
    })
    assert after_reset["rotation"] == 270
    assert "continuity_override" not in after_reset


def test_rotated_block_can_rescue_a_marginal_upright_choice():
    detector = AdaptiveOrient()
    detector._remember({"rotation": 270, "scores": {"270": 900.0}})

    result = detector._remember({
        "rotation": 0,
        "scores": {"0": 445.0, "270": 552.0},
    })

    assert result["rotation"] == 270
    assert result["continuity_override"] is True
