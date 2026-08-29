# OZY one-form verification

The initial OZY membership form now collects all member-supplied onboarding data
in one Discord modal:

1. Exact Total Battle roster name
2. Preferred language
3. Guardsmen level
4. Monsters level
5. Specialists level

Discord modals support up to five top-level components, so this uses the full
native modal capacity.

Flow:

Join -> roster suggestion -> complete form -> leadership approval -> access

The language and G/M/S data are stored when the exact roster name is accepted,
but no language/access role is granted while the claim is pending. Leadership's
verification card is refreshed to show the submitted profile. On approval,
existing role synchronization reads the stored language and grants the matching
language role automatically. Because the profile is already complete, the old
post-approval "Complete OZY profile" prompt is skipped.

Roster suggestions no longer submit a claim immediately. Selecting a suggested
roster name opens the same complete form with that name pre-filled.

`/verify` also always opens the complete form. If a name is supplied through the
slash-command autocomplete, it is used only to pre-fill the form.

`/profile` remains available after approval for future language/G/M/S edits.
