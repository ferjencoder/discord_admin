# Verification modal import hotfix

Live traceback identified the exact failure:

`NameError: name 'MembershipVerificationModal' is not defined`

`bot.py` referenced `MembershipVerificationModal(...)` inside
`_open_membership_verification()` but the architecture refactor omitted that
class from the `from ozy.discord_ui import (...)` block.

This package:
- adds `MembershipVerificationModal` to that import
- adds a regression test so this specific mistake cannot silently return

No state, Discord permissions, API, or verification logic changes are included.
