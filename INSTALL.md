# OZY Admin - hello/goodbye only member lifecycle

This patch removes OZY Admin from membership verification completely.

Automatic member lifecycle behavior becomes:

- Join -> themed hello in `START HERE/#welcome`
- Leave -> themed goodbye in `START HERE/#goodbye`

There is no automatic:
- roster lookup
- fuzzy name matching
- membership verification
- approval/rejection
- verification card
- game-name popup
- access-role synchronization from roster
- Special Access workflow
- roster `sync-roles` workflow

Discord Community Onboarding owns normal member access and language/G/M/S.

Profile/admin commands remain separate utilities:
- `/game-name`
- `/member-name`
- `/member-troops`
- `/members-json`

They are not part of joining and never control normal access.

## Important deployment check

The old live messages:

- `Verify OZY membership`
- `Roster verification pending`
- `After approval`
- `already linked to another Discord account`

do not exist in this source.

If they appear after this patch is committed and pushed, Render is running an
older deployment. Check the commit shown by Render against the local Git HEAD.

## Install

Replace the included files, preserving their paths.

Then run:

```bash
py -m pytest -q
py preflight_ozy_admin.py
```

Expected test result for this patch:

```text
66 passed
```

Commit and push, then confirm Render deploys that exact commit.

After redeploy, delete any historical verification messages/channels you no
longer want. Old Discord messages are not rewritten by a code deployment.

Recommended Discord cleanup:
- delete `ADMIN/#verification`
- rename `START HERE/#verification-help` to `#help` if you still want a public
  help channel
