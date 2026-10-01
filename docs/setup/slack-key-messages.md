# Saving keys by pasting them in Slack

Anyone in a client's Slack channel can post a message containing only:

```
KEY_NAME = key value
```

(one key per line, up to 10; add `for this channel only` to limit it to that channel). JTS PowerTool then:

1. saves each key to AWS Secrets Manager for that channel's client,
2. deletes the Slack message,
3. confirms in the channel by **name only** (never the value).

A value that starts with `sk-ant-` in a key named like `AXCEL_ANTHROPIC_AI_KEY` is stored as the client's `ANTHROPIC_API_KEY`,
the key the assistant uses for that client's replies (not billed by JTS). Any other name is stored under its own name.

The value is never written to the database, the activity log, the server log, the conversation memory or sent to Claude.
The conversation memory only records `[Key(s) NAME sent by Person; values hidden]`.

## One-time setup: let the bot delete the message

A bot can delete only its own messages. To delete the person's message, JTS needs a Slack **user token of a workspace Owner
or Admin** that is allowed to delete messages.

1. Open <https://api.slack.com/apps>, choose the JTS PowerTool app, then **OAuth & Permissions**.
2. Under **User Token Scopes**, add `chat:write`.
3. Click **Reinstall to Workspace** and approve it **while signed in as a workspace Owner or Admin**.
4. Copy the **User OAuth Token** (starts with `xoxp-`).
5. In the JTS dashboard, open **API keys** and add `SLACK_USER_TOKEN` with that value (never paste it in chat).
6. Restart: `sudo systemctl restart jts-powertool jts-worker`.

Check in Slack **Settings & administration → Workspace settings → Permissions → Message editing & deletion** that
Owners/Admins are allowed to delete other people's messages.

If the token is missing or Slack refuses, the key is still saved, and the bot asks the person to delete their own message.

## Notes

- The `SLACK_USER_TOKEN` belongs to a real person's account. If they leave the workspace, create a new one.
- This path trusts everyone in the client's channel (an owner decision). Every save is written to the activity log
  with the person's name (`SLACK_KEY_SAVED`), never the value.
- A message that is not exactly key lines (for example `API_URL = https://...` or normal chat) is never treated as a key.
- Slack, like any chat tool, may have delivered the text to its own systems for a moment before the bot deleted it.
  If a key was exposed for a long time (for example the delete failed), rotate it at the provider.
