# Test Plan

## Goals

- Verify team dinner scheduling behavior without requiring live Slack access.
- Keep live workspace identifiers out of committed files.
- Preserve a clear path for optional operator-run live validation.

## Automated Coverage

- Schedule parsing and next-run calculation.
- Poll date generation and vote toggling.
- Booking assignee selection.
- Settings modal parsing and validation.
- Slack action payload hardening.
- Google Calendar link and event payload generation.
- Scenario runner safety toggles.
- Public documentation and template neutralization.

## Commands

```powershell
python -B -m pytest tests/ -q -p no:cacheprovider
python -B -m pytest tests/test_scenarios_full.py tests/test_scenario_runner.py -q -p no:cacheprovider
python -m compileall -q app scripts tests main.py
```

## Optional Live Checks

Run live checks only in a private Slack test channel and keep all workspace
identifiers local. Prefer converting live findings into deterministic tests.
