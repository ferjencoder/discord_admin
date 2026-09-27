from ozy.member_messages import GOODBYE_MESSAGES, WELCOME_MESSAGES, normalize_language


EXPECTED_LANGUAGES = {"EN", "ES", "PT", "SV", "DE", "CEB", "FR", "RU", "AR", "NO"}


def test_all_onboarding_languages_have_multiple_welcome_messages():
    assert set(WELCOME_MESSAGES) == EXPECTED_LANGUAGES
    for code, messages in WELCOME_MESSAGES.items():
        assert len(messages) >= 5, code
        assert len({(message.title, message.body) for message in messages}) == len(messages)
        assert all("{member}" in message.body for message in messages)
        assert all("[OZY]" in message.body for message in messages)


def test_all_onboarding_languages_have_goodbye_messages():
    assert set(GOODBYE_MESSAGES) == EXPECTED_LANGUAGES
    for code, messages in GOODBYE_MESSAGES.items():
        assert len(messages) >= 3, code
        assert all("{member}" in message.body for message in messages)


def test_unknown_language_falls_back_to_english():
    assert normalize_language(None) == "EN"
    assert normalize_language("") == "EN"
    assert normalize_language("xx") == "EN"
    assert normalize_language("es") == "ES"
