# OZY Admin

Discord administration bot for the OZY Odyssey Total Battle clan.

## Membership model

```text
Discord Community Onboarding
  -> preferred language
  -> Guardsmen G1-G9
  -> Monsters M1-M9
  -> Specialists S1-S9

OZY Admin
  -> mirrors onboarding metadata into member profile state
  -> suggests/accepts exact Total Battle roster name
  -> leadership Approve / Reject
  -> grants Verified and roster leadership rank
```

Language and G/M/S roles are metadata only. They never grant clan access.
`Verified` and `Special Access` are the normal clan access gates.

Members change profile answers through Discord **Channels & Roles**. `/profile`
shows the mirrored structured values.

## Run locally

```bash
py -m pip install -r requirements.txt
py -m pytest -q
py bot.py
```

## Discord maintenance

Export live onboarding:

```bash
py tools/discord/onboarding.py export
```

Inspect the canonical configuration:

```bash
py tools/discord/onboarding.py show config/discord/onboarding.json
```

Runtime architecture and deployment details are under `docs/`.
