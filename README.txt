OZY Admin - Windows zoneinfo fix

Cause:
Windows Python does not ship the IANA timezone database used by zoneinfo.
The project uses ZoneInfo("America/Argentina/Buenos_Aires") and ZoneInfo("UTC"),
so the first-party tzdata package must be an explicit runtime dependency.

Changed:
requirements.txt
+ tzdata>=2025.2

Local update:
    py -m pip install -r requirements.txt

Verify:
    py -m pytest -q

Expected:
    52 passed

Render:
No command change is required. Its existing build command installs
requirements.txt, so tzdata will also be installed there.

This is a dependency fix only. No application code changes are required.
