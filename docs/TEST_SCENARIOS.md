# Test Scenarios

Use this matrix as a public-safe guide. Replace placeholders with your own
private workspace data only in local ignored files or environment variables.

| ID | Scenario | Expected Result |
| --- | --- | --- |
| A1 | Type `회식` in a test channel. | The settings/action panel is posted. |
| A2 | Mention the bot with `회식`. | The same user flow appears as plain invocation. |
| A3 | Open settings. | The settings modal opens with defaults. |
| A4 | Start a poll. | A poll message is posted with business-day date buttons. |
| A5 | Toggle a date button. | The user's unavailable vote is added or removed. |
| A6 | Close a no-vote poll. | The run is skipped or handled according to workflow rules. |
| A7 | Close a poll with votes. | A booking date and assignee are selected from eligible members. |
| A8 | Mark booking done. | The workflow is marked complete. |
| A9 | Cancel an active run. | The active workflow is cancelled. |

Do not commit live Slack IDs or screenshots captured while running these
scenarios.
