# Test Results

This file records public-safe verification guidance and should not contain live
Slack app IDs, channel IDs, team IDs, screenshots, or real user names.

## Recommended Non-live Verification

| Layer | Command | Expected Result |
| --- | --- | --- |
| Unit/integration | `python -B -m pytest tests/ -q -p no:cacheprovider` | All tests pass. |
| Scenario subset | `python -B -m pytest tests/test_scenarios_full.py tests/test_scenario_runner.py -q -p no:cacheprovider` | Non-live scenario tests pass. |
| Compile | `python -m compileall -q app scripts tests main.py` | Python files compile. |
| Publish audit | See `docs/PUBLISH_CHECKLIST.md` | No publishable internal identifiers or secrets. |

## Live Verification Policy

Live Slack UI checks are optional and should be performed only in a private test
workspace or channel controlled by the operator. Keep live evidence local:

- Do not commit screenshots.
- Do not commit channel IDs, team IDs, app IDs, or user IDs.
- Do not commit real user names from your workspace.
- Generalize findings into regression tests when possible.

## Current Public Summary

The public test suite covers scheduling, settings, poll interactions, booking
assignee selection, Google Calendar link generation, malformed Slack payload
handling, and publication-safety guardrails.
