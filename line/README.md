# Line channel files (line/1.6/)

| file | read by | purpose |
|---|---|---|
| `stable.json` | every device of the line (via `/etc/ga-version-url`) and the final bake | the **proven** set. Changes only by the PROMOTE workflow (ga-ops `promote-line-channel`), merged by a human. |
| `rc.json` | **rc bakes only** (hassio.mk, label `-rcN`); no device polls it | the candidate set under test. Edit freely; it reaches no device until promoted. |
| `dev.json` | DSGW-210 dev images | see `scripts/check_line_channel.py` DEV-SCOPE |
| `promoted.json` | `scripts/check_line_channel.py` | which rc `stable.json` was promoted from, the digest of that content, and the gate evidence |

Flow (Odoo #1610): edit `rc.json` -> bake `-rcN` (reads `rc.json`, image still polls `stable.json`) ->
K31 fresh-flash gate green -> write the gate record (ga-ops) -> dispatch PROMOTE -> PR copies
`rc.json` to `stable.json` (channel renamed) and updates `promoted.json` -> human merges.

CI (`line-channel` workflow) fails a `stable.json` whose content digest differs from `promoted.json`,
so a hand edit of `stable.json` (the 2026-11-06 rc3 pattern) is red.
`check_line_channel.py --print-digest FILE` prints the digest (everything except `channel`).
