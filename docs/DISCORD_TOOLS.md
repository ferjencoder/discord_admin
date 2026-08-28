# Discord Maintenance Tools

These scripts are operator tools and are not imported by OZY Admin at runtime.

Run them from the repository root so they can read the same `.env`.

```bash
py tools/discord/export_roles.py
py tools/discord/export_channels.py
py tools/discord/onboarding.py export
```

Role changes should be dry-run first when supported.

The current declarative onboarding configuration is:

```text
config/discord/onboarding.json
```

Apply it with:

```bash
py tools/discord/onboarding.py apply config/discord/onboarding.json
py tools/discord/onboarding.py apply config/discord/onboarding.json --apply
```

Generated exports belong in `exports/` and are ignored by Git.
