# calendar-colors

Automatic, **frequency-aware** color + emoji styling for Google Calendar. It
classifies every event into a category (meeting, household, travel, interview,
fitness, …) and applies a consistent per-event color and title emoji, so a glance
at your week tells you *what kind* of thing each block is — across all your
calendars at once.

It's a small Python engine plus a single source-of-truth color scheme. Run it by
hand, or wire it to a daily `launchd` job so your calendar stays styled with zero
effort.

> Origin: this started as a personal [Claude Code](https://claude.com/claude-code)
> skill and was extracted into a standalone, shareable tool. `SKILL.md` is the
> original in-agent control-panel doc and is included for reference.

---

## Why

Google Calendar gives you **11 per-event colors**. Most people either don't color
events or hand-color a few. This automates it with a designed scheme and two ideas
that make a busy calendar readable:

- **Frequency-aware allocation** — your most frequent categories get their own
  solo color; only low-frequency and/or cross-calendar categories share one.
- **Semantic colors** — gray = low-salience background (town halls, location
  banners); red = protect-this-block (deep focus); blue = meetings; etc.

## The default scheme

| colorId | Google color | Categories |
|--:|---|---|
| 1 | Lavender | birthday, memorial |
| 2 | Sage | household, errands |
| 3 | Grape | partner, anniversary |
| 4 | Flamingo | social |
| 5 | Banana | school |
| 6 | Tangerine | travel |
| 7 | Peacock | regular_meeting |
| 8 | Graphite | broad_meeting, working_location |
| 9 | Blueberry | career, interview |
| 10 | Basil | health, fitness |
| 11 | Tomato | deep_work |

Colors and emoji live in **`calendar-colors.default.yml`** — the one place they're
defined. `taxonomy.py` loads it (plus your private overlay) and every consumer
imports the same values. Change a color in the YAML and it changes everywhere;
`taxonomy.validate_config` enforces the invariants (every shared color must be
declared; a soft warning fires if two *high-frequency* categories collide).

## How classification works

`gcal/calendar_maintenance.py` resolves each event to a category, in precedence
order:

1. **Learned rules** — exact-title overrides you curate.
2. **Provenance** — a source tag on the event.
3. **Interview / description signals** — a title keyword *or* a high-precision
   signal in the **description** (e.g. an ATS interview link). The classifier reads
   descriptions, not just titles — so a candidate interview titled only
   "Name – Role" is caught by the Lever/Greenhouse/Ashby link in its body.
4. **Keyword table** — an ordered, collision-guarded keyword router.
5. **Emoji hint** → **fallback**.

Work meetings are further split by size (≥17 attendees or a broadcast keyword →
`broad_meeting`, else `regular_meeting`). Purples are kept off the work calendar
except navy/Blueberry (which carries career + interview).

## Install

```bash
git clone https://github.com/ahasham/calendar-colors.git
cd calendar-colors
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python -m pytest        # optional: run the test suite
```

## OAuth setup

You need a Google OAuth token with the Calendar scope. Create an OAuth client in
the [Google Cloud console](https://console.cloud.google.com/) (Desktop app),
download the client secret, and run a standard installed-app OAuth flow to produce
a `token.json` containing `access_token`, `refresh_token`, `client_id`,
`client_secret`, and `token_uri`. Put it in your `oauth_dir` (see config below).
Read scope styles existing events; the full `calendar` scope is needed to write
colors.

## Configure

Copy the example overlay and fill in your calendars/tokens (keep it private — it's
gitignored):

```bash
cp calendar-colors.example.yml ~/.config/calendar-colors/config.yml
export CALENDAR_COLORS_CONFIG=~/.config/calendar-colors/config.yml
# edit the file: your calendar IDs, oauth_dir, token filenames, self_names
```

The shipped `calendar-colors.default.yml` already holds the full scheme — your
overlay only adds instance data and any personal color tweaks.

## Run

Preview (no writes):

```bash
python gcal/calendar_maintenance.py --calendars primary --window-days 365 --restyle-all
```

Apply (writes colors/emoji to your calendar; idempotent — a second run is a no-op):

```bash
python gcal/calendar_maintenance.py --calendars primary --window-days 365 --restyle-all --apply
```

Frequency report — the data behind the allocation rule (per-category counts over
the last ~6 months + 90-day recency):

```bash
python -m gcal.frequency
```

## Automation (macOS)

`launch-agents/` has two `.plist.template` files — a daily personal-styling job and
a daily work-calendar job. Replace the `__PLACEHOLDERS__`, drop them in
`~/Library/LaunchAgents/`, and `launchctl bootstrap`/`kickstart` them. (Linux users
can run the same commands from `cron`.)

## Notes & limits

- **11 event colors** is a hard Google API cap (calendar-*level* color offers 24 +
  custom hex, but that colors a whole calendar, not per-event — so it can't
  distinguish categories within one calendar).
- Styling only touches events in the scan window; recurring series are styled at
  the master. Events you're only invited to are colored on your copy.
- Duplicate detection is **propose-only** — the engine never deletes events.
- The `launchd` automation is macOS-specific; the engine itself is plain Python.

## License

MIT — see [LICENSE](LICENSE).
