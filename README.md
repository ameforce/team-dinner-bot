# Team Dinner Bot

Slack Socket Mode bot for team dinner scheduling. The bot opens an
unavailable-date poll, records conflicts, assigns one booking owner, and sends
that owner booking and Google Calendar guidance by DM.

## Local Run

```powershell
cd <repo>
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy config.example.env .env
# Fill .env with your Slack tokens.
python main.py
```

`.env` is local-only and must never be committed. If real Slack credentials were
ever present in your workspace before publication, rotate the bot token, app
token, and signing secret before creating or pushing a git repository.

Required Slack values:

- `SLACK_BOT_TOKEN`
- `SLACK_APP_TOKEN`
- `SLACK_SIGNING_SECRET`

Optional:

- `APP_NAME`, `APP_SLUG`, `BOT_DISPLAY_NAME`: public app identity defaults.
- `DATABASE_URL`, `LOCK_FILE_PATH`: local runtime storage paths.
- `ADMIN_USER_IDS`: comma-separated Slack user IDs allowed to run forced polls.
- `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REFRESH_TOKEN`,
  `GOOGLE_CALENDAR_ID`: enable server-side Google Calendar event creation.
  Without a refresh token, the bot falls back to a Google Calendar create link.

## Slack App Surface

Use `slack-app-manifest.json` as a starting point for your workspace. The
manifest scopes cover Socket Mode, channel events, messages, DMs, user email
lookup, and poll message updates.

Use your own private Slack test channel for local validation. Keep real app IDs,
channel IDs, team IDs, screenshots, and user names out of committed files.

## Local Operator Files

Some files are intentionally local-only:

- `.env`
- `AGENTS.md`
- `deploy/systemd/team-dinner-bot.service`
- runtime databases and lock files

Use the committed examples as templates:

- `config.example.env`
- `AGENTS.md.example`
- `deploy/systemd/team-dinner-bot.service.example`

This keeps clone-specific deployment details out of normal `git diff` output.

## Settings Policy

Channel members can open and submit the settings modal by design. This keeps
team dinner scheduling maintainable by the channel. Forced poll execution is a
separate control and can be restricted with `ADMIN_USER_IDS`.

## Poll And Calendar Behavior

Poll targets are assumed available by default. Users click only dates they
cannot attend. When the poll closes, the bot first chooses randomly among dates
with zero unavailable voters. If every date has conflicts, it chooses randomly
among the dates with the fewest conflicts.

After a poll closes, only the booking assignee receives the booking DM. When
Google OAuth refresh-token settings are configured, the bot creates the calendar
event through the Google Calendar API and includes required/optional attendees.
If direct creation is not configured or fails, the DM includes a Google Calendar
creation link as a fallback.

## Verification

```powershell
python -B -m pytest tests/ -q -p no:cacheprovider
python -B -m pytest tests/test_scenarios_full.py tests/test_scenario_runner.py -q -p no:cacheprovider
python -m compileall -q app scripts tests main.py
```

Before publication, follow `docs/PUBLISH_CHECKLIST.md`.
