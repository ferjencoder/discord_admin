# Simplified OZY onboarding

## Platform constraint

Discord Community Onboarding exposes only `MULTIPLE_CHOICE` and `DROPDOWN`
prompts. It cannot collect an arbitrary free-text Total Battle name.

Using Discord Server Member Applications would allow custom application
questions, but that workflow requires staff approval before the member joins,
which is intentionally not part of OZY's simplified flow.

## Production model

1. Native Discord Onboarding asks:
   - preferred language
   - Guardsmen level, shown G9 -> G1
   - Monsters level, shown M9 -> M1
   - Specialists level, shown S9 -> S1
2. The required language answer also grants the normal member-access role.
3. Normal clan channels open after onboarding.
4. ADMIN and LEADERSHIP remain restricted by their own role/category permissions.
5. OZY Admin does not verify, match, suggest, approve, reject or check members
   against the Total Battle roster.
6. The member's Discord **server nickname** is treated as the Total Battle name.
7. Members may change it through Discord or `/game-name`.
8. Leader/Superior may change another member with `/member-name`.
9. Leader/Superior may update G/M/S with `/member-troops`.
10. `/members-json` exports current Discord members plus language/G/M/S.

## Member events

On join, after native onboarding/screening completes, OZY Admin posts only a
themed welcome in `START HERE / #welcome`.

On leave, OZY Admin posts only a themed farewell in `START HERE / #goodbye`.

There is no membership verification UI and no staff approval queue.

## Game-data commands

Roster/chest APIs may still be used by chest/reporting features. They are not
used to decide whether a Discord member may enter or remain in the server.


## OZY branding in Discord Onboarding / Server Guide

`config/discord/onboarding.json` contains only the API-managed onboarding
questions/default channels and must not contain HOT branding.

Discord's **Server Guide Welcome Sign** is a separate part of the Onboarding UI.
If an old `HOT Clan` message is visible there, edit it in:

`Server Settings -> Onboarding -> Server Guide -> Welcome Sign`

Recommended text:

`Welcome to OZY - Odyssey. Enter the madhouse.`

The public Guild Onboarding API does not include the Server Guide Welcome Sign,
so the onboarding JSON/apply tool cannot rewrite that field.
