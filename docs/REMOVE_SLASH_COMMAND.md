# Removing A Slash Command

The bot can be operated without a slash command by using normal channel
messages or bot mentions. If you created a `/hoeshik` command while testing and
want to remove it:

## 1. Delete The Command

1. Open `https://api.slack.com/apps/<APP_ID>/slash-commands`.
2. Select `/hoeshik`.
3. Delete the command.

## 2. Update The Manifest

1. Open `https://api.slack.com/apps/<APP_ID>/app-manifest`.
2. Update the app with `slack-app-manifest.json`.
3. Confirm `slash_commands` is absent.
4. Reinstall the app to your workspace if Slack asks for it.

## 3. Validate

- Type `회식` in a private test channel.
- Mention your bot with `회식`.
- Click the settings button and confirm the modal opens.
