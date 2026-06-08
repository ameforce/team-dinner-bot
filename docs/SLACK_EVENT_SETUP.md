# Slack App Setup

Use `slack-app-manifest.json` as the starting point for a new Slack app in your
own workspace.

## 1. Slash Command

1. Open `https://api.slack.com/apps/<APP_ID>/slash-commands`.
2. Create `/회식`.
3. Leave the request URL empty when Socket Mode is enabled.
4. Use `회식 일정 설정, 상태 확인, 투표 실행` as the description.

## 2. Event Subscriptions

1. Open `https://api.slack.com/apps/<APP_ID>/event-subscriptions`.
2. Turn **Enable Events** on.
3. Subscribe to these bot events:
   - `member_joined_channel`
   - `member_left_channel`
4. Save changes.

Socket Mode does not require a public request URL.

## 3. OAuth Scopes

In **OAuth & Permissions -> Scopes -> Bot Token Scopes**, include:

- `chat:write`
- `channels:read`
- `commands`
- `groups:read`
- `im:write`
- `users:read`
- `users:read.email`

Reinstall the app to your workspace after changing scopes.

## 4. Invite The Bot

Invite your bot to a private test channel:

```text
/invite @Team Dinner Bot
```

## 5. Run Locally

```powershell
cd <repo>
python -m app.main
```

Run `/회식` or `/회식 설정` in your private test channel.
