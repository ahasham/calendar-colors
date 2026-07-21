---
name: calendar-colors
description: View, change, or re-apply the calendar + Gmail color/emoji scheme (the "styling system"). Use when the user asks "what color is X", "change/rethink a calendar color", "recolor my calendar", "why is my calendar purple", "add a category", "re-run calendar styling", or wants to understand how calendar/gmail auto-coloring works. This is the entry point to a system that is cron jobs + taxonomy.py, NOT a normal skill.
tier: gold
status: v1-built
created: 2026-07-15
---

# calendar-colors — the styling system control panel

> **Note:** this is the original [Claude Code](https://claude.com/claude-code)
> skill doc. File paths (`~/.claude/lib/...`), job labels (`com.example.*`), and
> some features it mentions (Gmail label styling, a `/calendar-colors setup` wizard,
> the T1/T2 plans) reflect the author's fuller personal setup and are **not all
> included in this standalone repo**. For installing and running *this* repo, see
> **[README.md](README.md)** — that's the source of truth for the shared package.

The one place to see and manage how Google Calendar events and Gmail labels get
their colors + emoji automatically. Everything below is driven by cron jobs and a
single source-of-truth module; this skill is the human/Claude entry point.

## Single source of truth
**`~/.claude/lib/calendar-colors.default.yml`** — the ONLY place colors/emoji are
defined (shipped scheme), with a personal overlay in
`~/.claude/config/calendar-colors.yml`. `lib/taxonomy.py` is the LOADER: it reads
those YAML files and exposes the same names every consumer imports (calendar_icons,
gcal/style, gcal/calendar_maintenance, events/occasions_sync, gmail/hygiene).
Change a color in the YAML and it changes everywhere. Never hardcode a color
elsewhere. `taxonomy.validate_config` enforces the invariants (distinct-except-
declared-merges — now over ALL colored categories incl. sub-types — and
forbidden_on_work). When passed a live frequency map it also soft-warns if two
high-frequency categories share a color (the frequency-aware allocation rule).

**Frequency-aware allocation (a first-class goal).** Colors are allocated by how
often a category actually occurs: the most-frequent categories hold SOLO colorIds;
a shared colorId (`rules.color_merges`) is only legitimate for low-frequency and/or
cross-calendar pairs, or a high-stakes category that claims its own color
regardless of volume (interview). ALWAYS pull real frequency before proposing any
color change:
```bash
~/.claude/lib/helper-venv/bin/python3 -m gcal.frequency   # per-category 6mo counts + 90d recency
```

To print the current scheme:
```bash
~/.claude/lib/helper-venv/bin/python3 -c "import sys;sys.path.insert(0,'$HOME/.claude/lib');import taxonomy as t;\
[print(f'{k:16} colorId={v[\"colorId\"]!s:>4} {v[\"emoji\"]}  gmail={v[\"gmail\"]}') for k,v in t.CATEGORIES.items()]"
```

## Current scheme (authoritative values live in taxonomy.py) — frequency-reallocated 2026-07-17
Per-event colorId (declared merges in parentheses):
Lavender(1)=birthday (+memorial) · **Sage(2)=household (+errands)** · Grape(3)=partner
(+anniversary) · Flamingo(4)=social · Banana(5)=school · Tangerine(6)=travel ·
Peacock(7)=regular_meeting · **Graphite(8)=broad_meeting (+working_location)** ·
**Blueberry(9)=career (+interview)** · **Basil(10)=health (+fitness)** · **Tomato(11)=deep_work (solo)**.
social + school are SOLO (no overlap with interviews or the child's schedule).

Work calendar (a color-only secondary/work account, `--emoji-off`):
- regular / small meeting → **Peacock (7) clean blue**
- **interview → Blueberry (9) navy** (the professional bucket, shared with career —
  interviews ARE hiring events; navy is the one purple-family color allowed on work.
  Detected by title keyword OR an ATS interview link in the DESCRIPTION —
  `hire.lever.co/interviews`, etc.)
- broad / big meeting (≥17 attendees or broadcast kw) → **Graphite (8) gray** (recedes)
- working_location (Home/Office/city) → Graphite (8)
- travel/flights → Tangerine (6) · HOLD/focus → **Tomato (11) red** (protect-only)

**Rules:** (1) content-icon overrides the category emoji, color always = category.
(2) **No purple for meetings on work** — Lavender(1, periwinkle) + Grape(3, magenta)
are banned on work (auto-recolored to gray). **Blueberry(9, navy) is ALLOWED** — it
reads as dark blue, not purple, and carries the career/interview professional bucket.
(3) **Gray = recede**
(low-salience background: town halls + location banners). (4) **Red = protect-only**
(deep_work / do-not-disturb — never routine things like interviews). (5) **Frequency
drives allocation** (see above). (6) per-event colorId is hard-capped at Google's 11
(no custom hex on events). NOTE: Peacock(7) is no longer *reserved* for work — it's
just where regular_meeting sits; personal categories may use it (nothing does today).

## The moving parts
| Piece | What |
|---|---|
| `lib/taxonomy.py` | color/emoji SSOT + `CATEGORIES`, `CALENDAR_CATEGORY_STYLE`, `OPERATIONAL_LABEL_COLORS`, `color_id()/emoji()` |
| `lib/gcal/calendar_maintenance.py` | the styling engine (classify → color/emoji); `--restyle-all`, `--learn-unknowns`, `--prune`… classify reads title + high-precision DESCRIPTION signals (`_DESC_CATEGORY_SIGNALS`) |
| `lib/gcal/frequency.py` | `python3 -m gcal.frequency` — per-category frequency census (allocation data + high-freq-collision warning); `--json` adds `retirement_candidates` for the weekly loop |
| `lib/gcal/allocator.py` | dynamic-taxonomy solver: allocates the 11 colors under semantic-anchor pins + frequency + hysteresis; classifies changes additive-vs-recolor; writes additive to the user overlay, queues recolors. CLI: `allocator.py --frequency <census> --candidates <cand> --apply-additive` |
| `com.example.calendar-maintain-daily` | 5:40am — styles primary+shared+school+events (`--restyle-all`) |
| `com.example.calendar-work-color-daily` | 6:10am — work cal, color-only (`--emoji-off --default-category career`) |
| `com.example.calendar-learn-weekly` | Sun — LLM maps new event types → `learned_styles.json` AND proposes new categories/retirements → `allocator.py` (dynamic taxonomy) |
| `com.example.cron-staleness-watchdog` | 10am — monitors all jobs (staleness + errored) |
| `lib/gmail/hygiene.py` + `gmail-hygiene-weekly` / `gmail-daily-flags` | Gmail label colors + filters (server-side) |
| `gold/Research/Google Calendar beautification methodology.md` | full design rationale (v1→v3) |
| tests | `lib/tests/test_calendar_style.py`, `lib/gcal/tests/test_calendar_maintenance.py` |
| plists (version-controlled) | `~/.claude/launch-agents/` (+ restore README) |

All jobs MUST run `~/.claude/lib/helper-venv/bin/python3` (system/homebrew python lack `yaml`).

> **Post-T1 (2026-07):** colors/emoji are no longer hardcoded in `taxonomy.py` —
> they load from `lib/calendar-colors.default.yml` (shipped scheme) with a personal
> overlay in `~/.claude/config/calendar-colors.yml` (calendars, accounts, tokens,
> self_names — the ONLY file with personal data). `taxonomy.py` is now a loader.
> Keyword routing (`_CATEGORY_KEYWORDS`) intentionally stays in code. See
> `skills/calendar-colors/T1-refactor-plan.md` + `T2-plan.md`.

## Common actions
**Change a color / emoji:** edit the category in `lib/calendar-colors.default.yml`
`categories:` (shipped default) — or add a `categories:` overlay in
`~/.claude/config/calendar-colors.yml` for a personal-only change. Run the tests
(`pytest lib/tests/test_calendar_style.py lib/tests/test_calendar_config.py lib/gcal/tests/`),
then re-apply (below). `validate_config` enforces the invariants (distinct colors
except declared merges, no purple on work).

**Re-apply after a change (idempotent):**
```bash
launchctl kickstart -k gui/$(id -u)/com.example.calendar-maintain-daily     # personal
launchctl kickstart -k gui/$(id -u)/com.example.calendar-work-color-daily    # work
```
Or run the engine directly with `--restyle-all --apply` (see the plist for exact args).

**Add a new category:** add to `calendar-colors.default.yml` `categories:` (pick a
free/within-domain colorId + emoji; declare a merge in `rules.color_merges` if it
shares a colorId), add keyword routing in `calendar_maintenance._CATEGORY_KEYWORDS`,
add a test, re-apply.

**Check health:** `~/.claude/lib/helper-venv/bin/python3 ~/.claude/lib/cron-staleness-check.py`

## Setup a NEW install (`/calendar-colors setup` — T2 wizard)
The scriptable steps live in `lib/gcal/setup_wizard.py`; the human decisions
(consent, per-calendar features) are yours via AskUserQuestion. Procedure:
1. **OAuth** — if no broad-scope token, have the user run (their consent click):
   `~/.claude/lib/helper-venv/bin/python3 ~/.claude/lib/gcal/authorize.py --token-out <oauth_dir>/token.json`
   (defaults to full `calendar` scope: styling + discovery + calendar-level color).
2. **Discover** — `setup_wizard.py discover --account default` → JSON calendars.
   If `scope_ok:false` (events-only token), fall back to manual calendar-id entry.
3. **Map features** — AskUserQuestion per discovered calendar: style it? emoji
   full / color-only / off? title_rewrite llm/off? account (default/work)?
   default_category? Assemble a choices JSON.
4. **Generate (staging)** — `setup_wizard.py generate --choices <f> --stage-dir <dir>`
   → writes config + plists + retargeted scripts to a staging dir (NOT live).
5. **Preview** — `setup_wizard.py preview --config <stage>/config/calendar-colors.yml`
   → "would_restyle: N". Show the user before arming.
6. **Install (on approval)** — copy staged config → `~/.claude/config/`, staged
   plists → `~/Library/LaunchAgents/` (+ repo `~/.claude/launch-agents/`), scripts
   → `~/.claude/lib/`; then `launchctl bootout`/`bootstrap` + `kickstart -k` each
   job and verify exit 0 (the T1 walkthrough procedure). macOS/launchd only.

## Dynamic taxonomy — categories learn/retire themselves
The category set is not a fixed hand-curated list. The weekly learn job evolves it
under the 11-color ceiling via three decoupled layers: **categories** (unbounded,
learned), **colors** (capped at 11, allocated by `gcal/allocator.py`), **emoji**
(unbounded, always unique — so a new category is visually distinct immediately and
shares a color until it earns a solo slot). Weekly flow: `frequency.py --json`
census → `--learn-unknowns` (emits proposal context) → the LLM maps titles into
existing categories OR proposes NEW categories / RETIREMENTS → `allocator.py
--apply-additive`.

**Apply autonomy:** the allocator classifies each change.
- **ADDITIVE (auto-apply):** a new category taking a unique emoji + sharing a
  sibling's color (no incumbent recolored), or retiring a dead solo category →
  written straight to the user overlay.
- **INCUMBENT RECOLOR (gate):** any change that moves a live category's colorId →
  queued to `state/calendar/pending_recolors.json` for human approval.

**Anti-churn:** hysteresis — a solo-promotion or retirement must HOLD across
`hysteresis_weeks` / `retire_dead_weeks` consecutive censuses before it fires
(state in `state/calendar/allocation_state.json`). Semantic anchors (11=protect/red,
8=recede/gray, 9=professional/navy) are pinned, never frequency-reassigned. A
solo-promotion is only proposed when the candidate names an explicit
`demote` + `demote_into` — the solver never fabricates an incumbent demotion.

## When invoked
1. Show the current scheme by loading it: `python3 -c "import taxonomy; ..."`
   (taxonomy loads `calendar-colors.default.yml` + the user overlay — the loaded
   values are truth; don't trust prose copies, they drift).
2. **Check for pending recolors:** if `state/calendar/pending_recolors.json` has a
   non-empty `changes[]`, present them for approval (gated scheme changes from the
   weekly loop). On approval: `allocator.apply_changes` + `write_user_overlay` →
   run tests → re-apply the jobs. Then empty the queue.
3. If the user wants a change, edit `calendar-colors.default.yml` (or a
   `categories:` overlay in the user config), run the tests, re-apply via the jobs,
   and confirm idempotent (`--restyle-all` → restyle=0 on re-run).
