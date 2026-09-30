# Dedicated Merc bot on Render Free

Create a Web Service named `ozy-merc-bot` using the `discord_admin` repository.
Choose Python, branch `main`, and the Free instance type. Leave Root Directory
blank. Build command: `pip install -r requirements.txt`. Start command:
`python merc_bot.py`. Health check path: `/healthz`.

Use `.env.mercs.example` for this service's environment variables. Set
`MERCS_DISCORD_TOKEN` to the new Discord application's bot token privately.
Reuse the existing `OZY_DATA_API_TOKEN`. No calendar, roster or Admin settings
are required. Invite the bot to the server and grant View Channel and Send
Messages in `ozy_merc_yooo` (1554666038493323324).

The health server listens on Render's PORT. Render supplies RENDER_EXTERNAL_URL;
the self-ping starts after 30 seconds and repeats every 600 seconds, matching
the existing Admin and Translator pattern. It cannot wake a process that has
already stopped. Free services can sleep/restart and consume shared free hours;
self-pinging is not a guarantee of continuous uptime.

The `/healthz` response includes Discord readiness, feed status, fresh record
count, last successful API read and last post time. A 200 response alone only
confirms that the web process is alive. Check that `discord_ready` is true,
`feed_status` is `polling`, and `last_api_success` keeps advancing.

The dedicated entry point registers no Admin commands and only sends code blocks
containing coordinates. It reuses the tested Merc parser/deduplication logic.
Run only one Merc service. Clear MERCS_CHANNEL_ID on the Admin service before
enabling this service, to avoid two bots posting the same sightings.

## Posting history

The bot uses a separate local SQLite file. Render Free has ephemeral storage,
so local history survives process restarts only while the filesystem remains.
Redeploys or spin-down can erase it and allow previously posted fresh sightings
to be posted again. For durable history, configure MERCS_STATE_REMOTE_URL and
MERCS_STATE_REMOTE_TOKEN against a separately provisioned compatible snapshot
endpoint. Do NOT reuse Admin's state endpoint: two writers can overwrite state.
This change does not create or modify a website endpoint.

Commit and push the new entry point before deploying. The old start command
`python bot.py` launches Admin instead of this dedicated Merc service.
