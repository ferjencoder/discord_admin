# OZY onboarding - descending troop levels + branding check

Changes:

- Guardsmen choices display G9 -> G1.
- Monsters choices display M9 -> M1.
- Specialists choices display S9 -> S1.
- Onboarding lint now rejects stale `HOT` branding in the API-managed onboarding JSON.
- Tests enforce the descending order.
- Documentation explains the separate Discord Server Guide Welcome Sign.

## Install

Replace the included files preserving their paths.

Run:

```bash
py -m pytest -q
py tools/discord/onboarding.py show config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json
```

Expected tests:

```text
69 passed
```

The dry run should show G9..G1, M9..M1, S9..S1.

Then apply:

```bash
py tools/discord/onboarding.py apply config/discord/onboarding.json --apply
```

## HOT Clan message

The current API-managed onboarding JSON and recent onboarding exports contain no
`HOT` branding.

If new members still see `HOT Clan`, it is in Discord's separate **Server Guide
Welcome Sign**, not the Guild Onboarding prompts JSON.

Change it in Discord:

`Server Settings -> Onboarding -> Server Guide -> Welcome Sign`

Recommended:

`Welcome to OZY - Odyssey. Enter the madhouse.`

Discord's documented Guild Onboarding API exposes prompts, default channels,
enabled state and mode. It does not expose the Server Guide Welcome Sign, so
that text should be changed in the Discord UI.
