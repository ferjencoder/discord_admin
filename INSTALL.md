# OZY Admin - remove roster gating from onboarding

## Discord limitation

Community Onboarding only supports multiple-choice and dropdown prompts. It
cannot collect arbitrary free text such as a Total Battle player name.

This patch therefore uses the simplest no-approval model available:

1. Native onboarding asks language + G/M/S.
2. Every required language answer grants the normal Verified access role plus
   the selected language role.
3. Normal clan access opens from native onboarding alone.
4. ADMIN and LEADERSHIP remain restricted by their own role permissions.
5. OZY Admin's game-name field is profile information only.
6. The game name is stored exactly as entered:
   - no roster lookup
   - no fuzzy matching
   - no uniqueness check
   - no stable Total Battle identity check
   - no access decision
7. `/game-name` changes your own name.
8. `/member-name` lets Leader/Superior change another member's name.
9. `/member-troops` updates another member's G/M/S.
10. `/members-json` exports current Discord members with game name, language,
    Guardsmen, Monsters and Specialists.

Legacy stable Total Battle IDs are cleared the next time a free-form game name
is saved, so an old account link cannot produce the previous "already linked to
another Discord account" error.

## Install

Replace the included files, preserving paths.

Run:

```bash
py -m pytest -q
py tools/discord/onboarding.py show config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json
```

The onboarding dry run should show every language answer with two roles, e.g.:

```text
English
  roles: EN, Verified
```

Then apply:

```bash
py tools/discord/onboarding.py apply config/discord/onboarding.json --apply
```

Then deploy the bot.

Important: Discord messages already posted by an older bot deployment are not
rewritten automatically. Delete the old test welcome message shown in Discord,
or test with a fresh join after deploying this patch.
