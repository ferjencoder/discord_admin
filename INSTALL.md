# Install

Replace the included files in the discord_admin repository, preserving paths.

Then delete every path listed in DELETE_THESE.txt.

No Discord onboarding reconfiguration is needed. The successful live native
onboarding configuration is already represented by config/discord/onboarding.json.

Run:

```bash
py -m pytest -q
py tools/discord/onboarding.py show config/discord/onboarding.json
py preflight_ozy_admin.py
```

Then commit/push and redeploy Render.

## New runtime boundary

Discord Community Onboarding is the member-facing source for:
- preferred language
- Guardsmen
- Monsters
- Specialists

Those answers are represented by zero-permission metadata roles.

OZY Admin automatically mirrors a complete selection into member_profiles with:
`profile_source = discord-onboarding`.

OZY Admin remains responsible for:
- greeting the new member
- suggesting/accepting the exact Total Battle roster identity
- leadership Approve / Reject
- Verified + Leader/Superior access/rank synchronization

Selecting a suggested roster name now submits the identity claim immediately.
The fallback verification modal asks only for the Total Battle name.

`/profile` is now read-only and tells members to change profile choices through
Discord Channels & Roles.
