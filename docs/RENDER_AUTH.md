# Render password verification

Status: implementation and local tests ready; deployment, shared secret setup,
remote tests and wiring the Cloudflare login/session routes are still required.
This does not by itself enable login on the Cloudflare website.

## Architecture

Use the existing OZY Admin Render web service (`python bot.py`), not an additional
paid service. `/internal/auth/v1` performs fixed-cost scrypt verification and
hash creation in a worker thread. It stores no credentials, issues no sessions,
and has no authority to grant roles. The caller must load the stored credential,
validate active membership and permissions, and handle session issuance/revocation.
Personal research remains restricted to personal PIN sessions; Admin still uses
the separate password. Legacy salted-SHA256 records can stay on Cloudflare until
a separately planned credential upgrade.

The shared service credential is unrelated to member PINs, the Admin password,
Discord's bot token, or the existing data API token. Never reuse those secrets.

## Deployment

1. Commit and push the Discord Admin changes and deploy that commit to the existing
   `ozy-admin` Render service. Existing health checks and start commands are unchanged.
2. Generate a random 32-byte secret (64 lowercase hex characters). Store it as
   `OZY_AUTH_SERVICE_TOKEN` in Render and as a Wrangler secret of the same name
   for `ozy-clan-website`. Do not commit or send it in chat. The verifier returns
   503 while the secret is absent. Invalid configuration rejects startup.
3. Set Cloudflare `OZY_AUTH_SERVICE_URL` to the confirmed HTTPS `.onrender.com`
   origin of that existing service, without a path, query, or user information.
4. Configure the `AUTH_ATTEMPT_LIMITER` Workers binding with limit 10 / 60 seconds.
   Use a namespace unique to this application's login limiter.
5. Test unauthorized calls (401), correct and incorrect dummy credentials, hash
   creation, busy responses and health checks on the deployed Render instance.
   Observe memory with the bot connected; only one hash job may run at a time.
6. Wire Cloudflare's authenticated login/reset/unlock handlers to `passwordWork`
   in `cloudflare/render-auth.mjs`, passing a server-resolved member ID and the
   Cloudflare-provided connecting IP. Import real auth data only through a private
   migration. Run the full member/admin/session tests before enabling these routes.
   Never expose `passwordWork` as an unrestricted public hashing proxy.

## Limits and failure handling

- Same scrypt settings as the current Node implementation; salt is text, not
  decoded hex. Node/Python interoperability is covered by tests.
- Maximum 4 KiB request, 512-byte credential and fixed hashing parameters.
- One CPU job, no waiting queue. Thread work keeps Discord's event loop responsive;
  disconnected callers do not release the memory slot before the work finishes.
- Render: 10 attempts/member/minute and 60 total/minute, in memory. Limits reset on
  restart and apply per process; run one bot process. This is an extra layer, not
  the website's sole brute-force protection.
- Cloudflare: HMAC-pseudonymous member/IP rate keys before the Render request.
  Workers native rate limits are per location and approximate; persist security
  lockouts with the auth/session implementation where required.
- 12-second upstream timeout, no redirects, no automatic retries. Cold starts can
  exceed that timeout; show a retry message, never bypass verification.
- No routine page-view traffic to Render; only password/PIN checks or credential
  creation. Reuse validated sessions for ordinary navigation.
- No request-body or credential logging. No CORS response permits browser clients.

Render's free allowance and sleep behavior still apply; reusing the existing
service avoids a second instance consuming the shared monthly service hours.
The existing bot's baseline memory and live capacity have not yet been measured.

## Validation

Discord repo: `python -m pytest tests/test_auth_verifier.py -q`

Website repo: `node --test tests/render-auth.test.mjs`

References:
- https://render.com/docs/free
- https://developers.cloudflare.com/workers/runtime-apis/bindings/rate-limit/

## Verified Render service and cost baseline (2026-10-09)

Service: discord_admin, srv-da4n9pojo6nc73dhging, https://discord-admin-4i3h.onrender.com, Free plan (512 MB, 0.15 CPU). Workspace IDEAS Tech Support has one service. Billing showed 180.65 / 750 instance hours, 101 MB / 5 GB outbound bandwidth, 0 / 500 pipeline minutes, no card on file, and a projected October bill of USD 0.

CPU and memory graphs require a paid plan, so baseline live memory has not been confirmed. The verifier now refuses work above 256 MiB RSS and logs only elapsed time, RSS, and process peak RSS. The authenticated status operation reports these process counters. This guard is precautionary, not proof against all bot memory spikes. Existing Free compute and billing settings are unchanged.
