# Prepublish Safety Checklist

Use this checklist before creating a git repository, committing, or pushing this
project. This pass intentionally does not run `git init`, create commits, push,
deploy, or start a local Socket Mode worker.

## Secrets

- Keep `.env` local only. Never commit it.
- Recreate `.env` from `config.example.env` when needed.
- Rotate Slack credentials before publication if real values were ever present
  in the workspace, including bot token, app token, and signing secret.
- Do not paste tokens, signing secrets, database contents, or screenshots of
  credential pages into docs, reports, commits, or terminal logs.

## Local-Only Files

The public repository should contain examples/templates, not local overlays:

- Commit `AGENTS.md.example`; keep `AGENTS.md` local and ignored.
- Commit `deploy/systemd/team-dinner-bot.service.example`; keep
  `deploy/systemd/team-dinner-bot.service` local and ignored.
- Keep real Slack app IDs, channel IDs, team IDs, screenshots, live evidence,
  and user names out of publishable files.

## Publish Root

Before future git initialization, confirm the publishable file set does not
contain:

- `.env` or local environment variants
- SQLite runtime databases or journal/WAL/SHM files
- lock files created by the running bot
- `.venv/`
- `.pytest_cache/`, `.ruff_cache/`, `__pycache__/`
- `.playwright-cli/`
- generated package metadata such as `team_dinner_bot.egg-info/`
- local OMX workflow artifacts, unless they are intentionally retained as
  ignored local evidence

Move recoverable local artifacts to a quarantine directory outside the project,
for example `C:\tmp\team-dinner-bot-publish-cleanup-YYYYMMDDTHHMMSS`.

## Public Name Scan

Scan both file contents and filenames in the publishable file set. Exclude
ignored local-only overlays such as `AGENTS.md` and the real systemd unit, but
include their `.example` templates.

Look for:

- former private branding
- internal host names
- personal or company-local paths
- real Slack app/channel/team/user IDs
- live-test user names or screenshots
- private key markers and token-like values

## Settings Policy

The channel settings modal is intentionally available to channel members. This
lets the dinner bot be maintained by the team in the channel. This is separate
from forced poll execution, which can be restricted with `ADMIN_USER_IDS`.

## Dependency Audit

This project currently uses environment-based dependency installation rather
than a lockfile-backed workflow. For a best-effort local CVE audit, run from a
clean environment:

```powershell
python -m pip install -e ".[dev]"
pip-audit --local
```

Record whether the command ran successfully. Do not claim lockfile-grade
coverage unless a lockfile workflow is added and audited.

## Verification Before Publication

Run non-live checks only unless you have intentionally coordinated a live Slack
test:

```powershell
python -B -m pytest tests/ -q -p no:cacheprovider
python -B -m pytest tests/test_scenarios_full.py tests/test_scenario_runner.py -q -p no:cacheprovider
python -m compileall -q app scripts tests main.py
```

`compileall` creates `__pycache__` directories. Move or remove those generated
bytecode directories again before the final publish-root audit.
