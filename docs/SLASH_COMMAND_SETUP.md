# Slash Command Setup

The bot is slash-command only. Channel messages such as `회식` and bot mentions
must not start the workflow.

## 1. Register The Command

1. Open `https://api.slack.com/apps/<APP_ID>/slash-commands`.
2. Create `/회식`.
3. Keep Socket Mode enabled. A public request URL is not required.
4. Save the command and reinstall the app if Slack asks for it.

## 2. Validate

- Run `/회식` in a private test channel.
- Run `/회식 설정` and confirm the settings modal opens directly.
- Type `회식` and mention the bot with `회식`; both should do nothing.
