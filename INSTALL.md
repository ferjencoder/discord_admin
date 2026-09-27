# OZY Admin - multilingual randomized welcome/goodbye messages

## Behavior

The bot now waits until native Discord Onboarding has produced a complete
language + G/M/S profile before posting the welcome.

This matters because `on_member_join` can happen before Discord has finished
assigning onboarding roles.

After onboarding is complete:

- selected language is read from the member's language role
- one of 5 OZY/Ozzy-style welcome messages is chosen randomly
- the message is posted in START HERE/#welcome
- the member is marked welcomed so later role edits do not trigger another hello

Supported languages:
EN, ES, PT, SV, DE, CEB, FR, RU, AR, NO

Goodbyes are also localized. The bot first checks the member's current language
role and then falls back to the stored onboarding profile if necessary.

There are:
- 5 welcome variants per language
- 3 goodbye variants per language
- English fallback if no known language can be resolved

No roster verification, approval flow, or access decision is introduced.

## Install

Replace the included files, preserving their paths.

Run:

```bash
py -m pytest -q
py preflight_ozy_admin.py
```

The reconstructed current source plus this patch passes:

```text
75 passed
```

Then commit/push and confirm Render deploys the new commit.
