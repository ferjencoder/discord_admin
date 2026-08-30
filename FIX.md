# Branded START HERE category fix

The live Discord category is named with decorative branding, e.g. `❖── 👋 START HERE`.
The previous bot/preflight required the category name to equal `START HERE` exactly, so
`#goodbye` was falsely reported as missing and the leave handler could fail to find it.

This patch matches category names ending in `START HERE`, while still requiring the channel
name itself to be exactly `goodbye` or `welcome`. Duplicate matches are not silently chosen.
