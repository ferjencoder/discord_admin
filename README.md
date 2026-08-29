# OZY Admin

Discord administration bot for the OZY Odyssey Total Battle clan.

## Simplified membership model

```text
Discord Community Onboarding
  -> preferred language
  -> Guardsmen G9-G1
  -> Monsters M9-M1
  -> Specialists S9-S1
  -> normal member-access role

OZY Admin member events
  -> fun welcome in START HERE / #welcome
  -> fun farewell in START HERE / #goodbye

Member profile
  -> game name = Discord server nickname
  -> no roster verification
  -> no approval queue
  -> no stable Total Battle identity link
```

Discord Community Onboarding supports only predefined multiple-choice/dropdown
answers, so it cannot collect an arbitrary Total Battle name. The zero-approval
model uses the member's Discord server nickname as that field instead.

Members can update it with `/game-name` or Discord's normal nickname editor.
Leaders/Superiors can correct it with `/member-name` and can update troop levels
with `/member-troops`. `/members-json` exports current Discord members with
nickname/game name, language and G/M/S.

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


Discord Server Guide branding is managed in the Discord UI. Keep its Welcome Sign branded OZY, never HOT.
