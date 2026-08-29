# OZY Admin

Discord administration bot for the OZY Odyssey Total Battle clan.

## Simplified membership model

```text
Discord Community Onboarding
  -> preferred language
  -> Guardsmen G1-G9
  -> Monsters M1-M9
  -> Specialists S1-S9

OZY Admin
  -> one final game-name field
  -> exact active-roster match or fuzzy suggestions
  -> immediate roster link
  -> Verified access
  -> Leader/Superior rank sync when applicable
```

There is no normal Leader/Superior approval queue.

Discord cannot collect free-text answers in native Community Onboarding, so the
Total Battle name is the only bot-assisted onboarding field. Language and G/M/S
remain native Discord answers represented by zero-permission metadata roles.

Members can update their own game name with `/game-name`. Leaders/Superiors can
correct names with `/member-name` and troop levels with `/member-troops`.
`/members-json` exports the active roster merged with Discord/profile data.

## Run locally

```bash
py -m pip install -r requirements.txt
py -m pytest -q
py bot.py
```

## Discord onboarding maintenance

```bash
py tools/discord/onboarding.py show config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json --apply
```

Runtime architecture and deployment details are under `docs/`.
