# OZY Admin - hard simplified onboarding

Replace the included files, preserving their paths, then delete the paths in
DELETE_THESE.txt.

## Resulting member flow

1. Discord Community Onboarding asks:
   - preferred language
   - Guardsmen level
   - Monsters level
   - Specialists level
2. OZY Admin greets the member with one `Set game name` button.
3. The member types the current Total Battle name.
4. Exact active-roster match -> link immediately -> Verified access.
5. Non-exact match -> closest active-roster names are offered for selection.
6. No Leader/Superior approval queue.

Discord Community Onboarding cannot collect arbitrary free-text answers, so
the Total Battle name remains the single bot-assisted field.

## Member maintenance

- `/game-name <name>` - member changes their own current game name. If a stable
  Total Battle user_id already exists, self-service changes must keep that ID.
- Language and G/M/S are edited through Discord Channels & Roles.

## Leadership maintenance

- `/member-name @member <game_name>`
- `/member-troops @member <G> <M> <S>`
- `/members-json`

`/members-json` returns an ephemeral JSON attachment with the active roster
merged with Discord identity, preferred language and G/M/S values.

## Apply the 4-question onboarding

First inspect/dry-run:

```bash
py tools/discord/onboarding.py show config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json
```

Then apply:

```bash
py tools/discord/onboarding.py apply config/discord/onboarding.json --apply
```

## Validate

```bash
py -m pytest -q
py preflight_ozy_admin.py
```

The build used for this package passed 64 tests.
