# OZY Discord Role Tools

Recommended model: Discord roles are for access/permissions; G/M/S are member profile data.

Core roles: Unverified, Verified, Special Access, Away, Soldier, Veteran, Officer, Superior, Leader.

Do not create G1-G9, M1-M9 and S1-S9 roles just to store member information.

## Requirements

```bash
pip install discord.py python-dotenv
```

`.env` must contain `DISCORD_TOKEN` and `SERVER_ID`. The bot needs Manage Roles, and OZY Admin must be above roles it edits.

## Export

```bash
python export_discord_roles.py
```

Creates JSON and CSV under `exports/`. Managed integration/bot roles are exported for reference but should not be recreated.

## Apply core roles

Dry run:

```bash
python apply_discord_roles.py ozy_roles_blueprint.json
```

Apply:

```bash
python apply_discord_roles.py ozy_roles_blueprint.json --apply
```

The apply tool creates missing roles, updates exact-name matches, skips managed roles, never deletes roles, and does not reorder roles.

The blueprint intentionally grants no broad permissions. Use category/channel permission overwrites for access control.

## Member profile

After exact roster-name verification, OZY Admin should collect Guardsmen (G), Monsters (M), and Specialists (S) levels and store them in the persistent member profile mirrored through ozy.com.ar. See `member_profile_schema.json`.
