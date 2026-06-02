# Slack Event Setup

Use `slack-app-manifest.json` as the starting point for a new Slack app in your
own workspace.

## 1. Event Subscriptions

1. Open `https://api.slack.com/apps/<APP_ID>/event-subscriptions`.
2. Turn **Enable Events** on.
3. Subscribe to these bot events:
   - `message.channels`
   - `message.groups`
   - `app_mention`
   - `member_joined_channel`
   - `member_left_channel`
4. Save changes.

Socket Mode does not require a public request URL.

## 2. OAuth Scopes

In **OAuth & Permissions -> Scopes -> Bot Token Scopes**, include:

- `app_mentions:read`
- `chat:write`
- `channels:history`
- `channels:read`
- `groups:history`
- `groups:read`
- `im:write`
- `users:read`
- `users:read.email`

Reinstall the app to your workspace after changing scopes.

## 3. Invite The Bot

Invite your bot to a private test channel:

```text
/invite @Team Dinner Bot
```

## 4. Run Locally

```powershell
cd <repo>
python -m app.main
```

Type `회식` or mention your bot with `회식` in your private test channel.
