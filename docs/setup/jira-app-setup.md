# Jira connection: one-time setup (JTS administrator)

JTS registers **one** Atlassian OAuth 2.0 (3LO) app. Every client then connects their own Jira Cloud site with one click
(`@bot connect my jira` in Slack, or **Keys & Connections → Jira → Connect Jira**). No client ever pastes a token.

## 1. Create the app

1. Open <https://developer.atlassian.com/console/myapps/> and sign in with a JTS Atlassian account.
2. **Create → OAuth 2.0 integration**. Name it `JTS PowerTool` and accept the terms.

## 2. Permissions (scopes)

1. **Permissions → Jira API → Add → Configure**.
2. Enable these three scopes and save:
   - `read:jira-work` (View Jira issue data)
   - `write:jira-work` (Create and manage issues)
   - `read:jira-user` (View user profiles)

(`offline_access`, which keeps the connection alive without asking again, is requested automatically by the connect link.)

## 3. Callback URL

**Authorization → OAuth 2.0 (3LO) → Configure**, set the Callback URL to exactly:

```
https://journeys.pe/api/jira/callback
```

(If `PUBLIC_BASE_URL` is different in AWS Secrets Manager, use that host instead.)

## 4. Let other companies authorize it

**Distribution → Edit**, set the status to **Sharing** and fill in the vendor name, a privacy policy URL and the
other required fields. Until this is done, only people in your own Atlassian organization can connect.

## 5. Save the two secrets (dashboard, never in chat)

Copy the **Client ID** and **Secret** from **Settings**. In the JTS dashboard open **API keys** and add:

| Key name | Value |
|---|---|
| `JIRA_OAUTH_CLIENT_ID` | Client ID |
| `JIRA_OAUTH_CLIENT_SECRET` | Secret |

Then restart the services so they pick the values up:

```bash
sudo systemctl restart jts-powertool jts-worker
```

## What a client sees

1. They click **Connect Jira**, sign in to Atlassian, choose their site, and click **Accept**.
2. JTS stores only the site id in the database. The refresh token goes to AWS Secrets Manager (hidden from the keys list).
3. The bot can now search and read issues directly. Creating, updating, commenting and moving issues always posts an
   approval card first.

## Notes

- Atlassian rotates refresh tokens; JTS stores the new one every time. A connection unused for 90 days expires, and the
  dashboard then shows **Needs reconnecting**.
- If a person offers several Jira sites, the first is used. Reconnecting lets them pick another.
- Disconnecting in the dashboard removes the stored token. To fully revoke, the client also removes `JTS PowerTool`
  under **Connected apps** in their Atlassian account.
