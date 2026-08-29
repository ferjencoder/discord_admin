# Simplified OZY onboarding

Normal members no longer wait for Leader/Superior approval.

## Member flow

1. Discord Community Onboarding collects preferred language and G/M/S levels.
2. Discord cannot collect free-text input in native Onboarding, so OZY Admin has one final `Set game name` field.
3. The entered name is checked against the active OZY roster. If it is not exact, the bot offers the closest roster names based on what the member typed.
4. An exact, unclaimed roster identity is linked immediately and `Verified` access opens.
5. Language/G/M/S remain editable in Discord Channels & Roles.

There is no normal approval queue and no Approve/Reject step.

## Name changes

- `/game-name <name>` lets a member update their own in-game name. If they already have a stable TB `user_id`, the new roster entry must have the same stable ID.
- `/member-name @member <name>` lets Leader/Superior correct a member link.

## Troop levels

- Members change G/M/S through Discord Channels & Roles.
- OZY Admin mirrors the metadata roles automatically.
- `/member-troops @member G M S` lets Leader/Superior correct them directly.

## JSON export

`/members-json` is leadership-only and returns an ephemeral JSON attachment containing every active roster member plus Discord link, language and G/M/S fields when known.

The old verification tables remain in SQLite only for migration compatibility. They are not used by the normal member flow.
