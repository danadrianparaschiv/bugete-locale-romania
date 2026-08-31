from bgconvertor.years import role_for_header


def test_comparative_header_roles_are_explicit():
    assert role_for_header("BVC 2023", budget_year=2024) == "buget_2023"
    assert role_for_header("EXEC.2023", budget_year=2024) == "executie_2023"
    assert role_for_header("BVC 2024", budget_year=2024) == "total_2024"
