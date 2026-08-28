# Install order

1. Back up the current discord_admin folder.
2. Replace the included complete files:
   - bot.py
   - state.py
   - settings.py
   - data_provider.py
   - .env.example (reference only - do not overwrite your real .env secrets)
3. Remove `TROOP_LEVEL_ROLE_MAP` from Render.
4. Add the `LANGUAGE_ROLE_MAP` value from OZY_LANGUAGE_ROLE_MAP.txt to Render.
5. Keep:
   TRUST_EXACT_DISPLAY_NAME=false
   AUTO_SYNC_NICKNAME=false
6. Apply the safe Community Onboarding config so Discord no longer grants language roles:
   py ozy_onboarding.py apply OZY_desired_onboarding_safe_2026-08-28.json
   py ozy_onboarding.py apply OZY_desired_onboarding_safe_2026-08-28.json --apply
7. Commit/push the discord_admin code and redeploy Render.
8. Test with a non-leadership Discord account.

Expected join flow:
Join -> Unverified -> roster suggestions -> claim -> leadership approval
-> Verified/rank -> Complete OZY profile -> language + G/M/S.

Validation performed before packaging:
- Python syntax compilation: passed
- Unit tests: 9 passed
- Safe onboarding lint: no OZY policy warnings
