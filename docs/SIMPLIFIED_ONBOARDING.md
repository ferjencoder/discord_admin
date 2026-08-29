# Simplified OZY onboarding

Discord Community Onboarding supports only MULTIPLE_CHOICE and DROPDOWN prompts.
It cannot collect an arbitrary text field such as a Total Battle player name.

The simplified production model is therefore:

1. Native Discord onboarding asks preferred language, Guardsmen, Monsters and Specialists.
2. Every required language answer grants both the language metadata role and the normal `Verified` access role.
3. Normal clan channels open immediately after onboarding. ADMIN and LEADERSHIP remain role-restricted.
4. OZY Admin asks for the Total Battle game name once after join. It stores exactly what the member enters.
5. The game name is profile information only. It is not checked against the website roster, is not unique, and never controls access.
6. Members can change it with `/game-name`; Leader/Superior can change another member with `/member-name`.
7. `/members-json` exports current Discord members with game name, language and G/M/S.

Legacy stable Total Battle IDs are cleared when a member saves a free-form game name, so old links cannot block a rejoin or a test account.
