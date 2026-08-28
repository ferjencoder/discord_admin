# OZY Admin - Post-verification Profile + Roster Suggestions

## Changed behavior

1. New members are kept Unverified.
2. OZY Admin compares Discord display/global/account names to the active roster and shows up to 5 likely Total Battle names.
3. Selecting a suggestion does not auto-verify. It submits an exact roster claim to leadership.
4. Leadership approval grants normal roster access/rank synchronization.
5. After approval, the member gets a `Complete OZY profile` button.
6. The profile collects preferred language plus G/M/S levels (1-9).
7. G/M/S are profile data only. No G/M/S Discord roles are created or managed.
8. Preferred language grants exactly one matching language role.
9. Unverified members have any language role stripped, preventing Community Onboarding from bypassing roster verification.
10. `/profile` lets an already verified member edit language/G/M/S later.
11. `/member` shows language, G, M and S.

## Render environment

Remove the obsolete variable:

```text
TROOP_LEVEL_ROLE_MAP
```

Add:

```text
LANGUAGE_ROLE_MAP=EN:1536541947173408839,ES:1536542118611263609,AR:1540062337111953448,DE:1536542327714095166,FR:1536542281593782292,NO:1540062640171126904,CEB:1536542372127572028,PT:1536542159459586128,SV:1536542203319681146,RU:1540062171965431949
```

Keep:

```text
TRUST_EXACT_DISPLAY_NAME=false
AUTO_SYNC_NICKNAME=false
```

## Discord Community Onboarding

Apply the safe Onboarding configuration that does not grant language roles. Language is now collected by OZY Admin only after roster approval.

## Deployment

Replace the included changed files in the current `discord_admin` project, commit, push, then redeploy Render.

No new Python dependency is required.
