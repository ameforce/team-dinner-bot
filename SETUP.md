# Local Setup

## 1. Prepare `.env`

```powershell
cd <repo>
copy config.example.env .env
notepad .env
```

Keep `.env` local only. Do not commit it, paste its values into docs, or include
it in screenshots/logs. Rotate real Slack credentials before publication if they
were ever present in your workspace.

Set these Slack values:

| Variable | Source |
| --- | --- |
| `SLACK_BOT_TOKEN` | Slack app OAuth & Permissions, Bot User OAuth Token |
| `SLACK_APP_TOKEN` | Slack app Basic Information, App-Level Token with `connections:write` |
| `SLACK_SIGNING_SECRET` | Slack app Basic Information, Signing Secret |

Optional identity and runtime values:

| Variable | Default |
| --- | --- |
| `APP_NAME` | `Team Dinner Bot` |
| `APP_SLUG` | `team-dinner-bot` |
| `BOT_DISPLAY_NAME` | `Team Dinner Bot` |
| `DATABASE_URL` | `sqlite:///./data/team-dinner-bot.db` |
| `LOCK_FILE_PATH` | `./data/team-dinner-bot.lock` |

## 2. Install

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## 3. Run

```powershell
python main.py
```

Expected log:

```text
team-dinner-bot starting (Socket Mode)...
```

## 4. Test Channel

Invite your bot to a private Slack test channel:

```text
/invite @Team Dinner Bot
```

Then run `/회식`, `/회식 help`, `/회식 status`, or `/회식 지금`.

## 5. Local Operator Files

```powershell
copy AGENTS.md.example AGENTS.md
copy deploy\systemd\team-dinner-bot.service.example deploy\systemd\team-dinner-bot.service
```

Both local copies are ignored. Customize them for your own host and process
manager without committing those environment-specific edits.

## 6. Calendar

Google credentials are optional. When `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, and `GOOGLE_REFRESH_TOKEN` are configured, the bot
creates a Google Calendar event directly. Without them, the booking assignee
receives a DM with a Google Calendar creation link.

## 7. Prepublish Checklist

Before future git initialization or publication, follow
`docs/PUBLISH_CHECKLIST.md`.
