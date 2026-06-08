# Test Scenarios

Use this matrix as a public-safe guide. Replace placeholders with your own
private workspace data only in local ignored files or environment variables.

| ID | Scenario | Expected Result |
| --- | --- | --- |
| A1 | Run `/회식` in a test channel. | The settings/action panel is posted. |
| A2 | Run `/회식 설정`. | The settings modal opens directly. |
| A3 | Type plain `회식` or mention the bot with `회식`. | No bot workflow is triggered. |
| A4 | Open settings from the panel. | The settings modal opens with defaults. |
| A5 | Start a poll. | A poll message is posted with business-day date buttons. |
| A6 | Toggle a date button. | The user's unavailable vote is added or removed without an extra confirmation message. |
| A7 | Close a no-vote poll. | The run is skipped or handled according to workflow rules. |
| A8 | Close a poll with votes. | A booking date and assignee are selected from eligible members. |
| A9 | Mark booking done. | The workflow is marked complete. |
| A10 | Cancel an active run. | The active workflow is cancelled. |

Do not commit live Slack IDs or screenshots captured while running these
scenarios.
