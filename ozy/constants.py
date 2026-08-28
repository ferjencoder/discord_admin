"""Shared OZY profile constants."""

PROFILE_LANGUAGES = (
    ("EN", "English"),
    ("ES", "Español"),
    ("PT", "Português"),
    ("SV", "Svenska"),
    ("DE", "Deutsch"),
    ("CEB", "Bisaya"),
    ("FR", "Français"),
    ("RU", "Русский"),
    ("AR", "العربية"),
    ("NO", "Norsk"),
)
PROFILE_LANGUAGE_CODES = {code for code, _ in PROFILE_LANGUAGES}
PROFILE_LEVELS = tuple(range(1, 10))
