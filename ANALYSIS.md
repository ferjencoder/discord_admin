# Membership/onboarding audit

The uploaded repository still contained remnants of older member-verification
architectures even though native onboarding had already been simplified.

Removed from the live member path:
- game-name modal/button on welcome
- roster matching/suggestions for member entry
- stable Total Battle identity linking for access
- approval/rejection concepts
- Special Access command
- roster-based `/sync-roles` command
- roster-access synchronization helpers

Kept:
- native language + G/M/S onboarding
- normal member-access role granted by required language answer
- Leader/Superior authorization for staff commands
- `/game-name` as a nickname convenience, not verification
- `/member-name`, `/member-troops`, `/members-json`
- roster/chest APIs for unrelated game-data/reporting features

The SQLite schema still contains old verification/link tables for backward
compatibility with existing production snapshots. They are not used by the new
join/leave flow. Dropping them is deliberately deferred because it adds database
migration risk with no user-facing benefit.
