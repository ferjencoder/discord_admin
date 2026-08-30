# Render command-sync startup fix

Root cause:

`setup_hook()` required these slash commands:

- `/event-create`
- `/calendar`
- `/today`
- `/time`

The latest member-lifecycle cleanup accidentally removed the registration blocks
for `/calendar`, `/today`, and `/time` while leaving the startup assertion in
place.

Discord synced 13 commands successfully, then OZY Admin intentionally aborted
startup because those three required commands were absent.

This patch restores only those three calendar/time commands. It does not restore
roster verification, approval queues, Special Access, or roster-based role sync.

It also strengthens the regression test so every command in `required_commands`
must actually be registered.

Expected after deployment:

`Synced 16 application commands ...`

then normal Gateway connection and `OZY Admin operational`.
