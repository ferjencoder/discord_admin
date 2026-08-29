from ozy.onboarding_profile import extract_onboarding_profile


def test_extract_complete_native_onboarding_profile():
    selection = extract_onboarding_profile(
        [
            (10, "EN"),
            (20, "G9"),
            (21, "M8"),
            (22, "S7"),
        ],
        {"EN": 10, "ES": 11},
    )
    assert selection.complete is True
    assert selection.preferred_language == "EN"
    assert selection.guardsmen_level == 9
    assert selection.monsters_level == 8
    assert selection.specialists_level == 7


def test_duplicate_troop_metadata_is_rejected():
    selection = extract_onboarding_profile(
        [(10, "EN"), (20, "G8"), (21, "G9"), (22, "M8"), (23, "S7")],
        {"EN": 10},
    )
    assert selection.complete is False
    assert "multiple G roles" in selection.issues


def test_language_role_name_fallback_works():
    selection = extract_onboarding_profile(
        [(999, "ES"), (20, "G6"), (21, "M5"), (22, "S4")],
        {},
    )
    assert selection.complete is True
    assert selection.preferred_language == "ES"
