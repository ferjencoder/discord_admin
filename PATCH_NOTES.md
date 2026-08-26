# OZY Admin - Onboarding / Roster / Member Profile Patch

## What changed

- New-member access starts locked until an active OZY roster identity is approved or Special Access is granted.
- Exact Total Battle name is validated against the authoritative roster API/data layer.
- Invalid names get a retry flow asking for the precise current clan name.
- Stable Total Battle `user_id` is preserved as the durable roster identity when available.
- Existing approved links are revalidated against the active roster on rejoin before access is restored.
- Discord Community Onboarding troop-level roles (`G1`-`G9`) can be mapped with `TROOP_LEVEL_ROLE_MAP`.
- Troop level is stored in `member_profiles` and updated when mapped Discord roles change.
- `/verify` without arguments opens the verification modal; `/verify game_name:<name>` still works.
- `/member` shows troop level when available.
- Startup command sync validation was restored.
- Calendar runtime naming/settings regressions were corrected and Today remains R+0-to-R+0 at 17:00 UTC.

## Required environment addition

Example only - replace with your real Discord role IDs:

```env
TROOP_LEVEL_ROLE_MAP=G1:111111111111111111,G2:222222222222222222,G3:333333333333333333,G4:444444444444444444,G5:555555555555555555,G6:666666666666666666,G7:777777777777777777,G8:888888888888888888,G9:999999999999999999
```

## Recommended Discord Onboarding question

Question: `What is your highest troop level?`

- Required: Yes
- Multiple answers: No
- Answers: G1, G2, G3, G4, G5, G6, G7, G8, G9
- Each answer assigns exactly one matching troop-level role.

Do not use Community Onboarding as the exact Total Battle-name authority. OZY Admin's verification modal collects the free-text exact name and checks it against the active roster.

## Persistence

The patch stores operational member profile data in the existing SQLite state database. Do not make a writable JSON file the canonical profile store. On an ephemeral Render filesystem, migrate this state to persistent storage/Postgres before relying on it as permanent member metadata. A JSON profile file can be generated later as a read model/export if the website needs it.

## Validation

- `pytest -q`: 40 passed
- Python compile checks: passed
- `git diff --check`: passed
