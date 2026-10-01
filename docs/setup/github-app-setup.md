# Setup: register the "JTS PowerTool" GitHub App (one time, ~15 minutes)

This lets every client connect their own GitHub with one click (from Slack or the dashboard).
You do it **once** for JTS. Never paste the private key or client secret into chat, email or the repository.

## 1. Create the app on GitHub

1. Sign in to GitHub with the account or organization that should **own** the app (JTS's org is best).
   - Organization: **Settings → Developer settings → GitHub Apps → New GitHub App**
   - Personal account: **Settings → Developer settings → GitHub Apps → New GitHub App**
2. Fill in:

| Field | Value |
|---|---|
| GitHub App name | `JTS PowerTool` (must be unique on GitHub; add a suffix if taken) |
| Homepage URL | `https://journeys.pe` |
| Callback URL | `https://journeys.pe/api/github/callback` |
| Expire user authorization tokens | ✔ checked |
| Request user authorization (OAuth) during installation | ✔ **checked** (required, it proves who installed the app) |
| Enable Device Flow | unchecked |
| Setup URL | leave empty (greyed out when the OAuth option above is checked) |
| Redirect on update | ✔ checked |
| Webhook → Active | ✔ checked |
| Webhook URL | `https://journeys.pe/api/github/webhook` |
| Webhook secret | a long random string (see step 3); keep it for step 4 |

3. To make a random webhook secret, run this on the server:
   ```bash
   openssl rand -hex 32
   ```

4. **Repository permissions**:

| Permission | Access |
|---|---|
| Administration | Read and write (lets the bot create website repositories later) |
| Contents | Read and write |
| Issues | Read and write |
| Metadata | Read-only (automatic) |
| Pages | Read and write |
| Pull requests | Read and write |
| Workflows | Read and write |

5. **Subscribe to events:** Installation target (if shown) and **Installation repositories**. (Installation events are always sent.)
6. **Where can this GitHub App be installed?** → **Any account** (so clients can install it on their own GitHub).
7. Click **Create GitHub App**.

## 2. Collect the values

On the app's **General** page:

| Secret name (Secrets Manager) | Where to find it |
|---|---|
| `GITHUB_APP_ID` | "App ID" (a number) |
| `GITHUB_APP_SLUG` | the last part of the app's public URL `https://github.com/apps/<slug>` |
| `GITHUB_APP_CLIENT_ID` | "Client ID" (starts with `Iv`) |
| `GITHUB_APP_CLIENT_SECRET` | **Generate a new client secret** → copy it immediately (shown once) |
| `GITHUB_APP_PRIVATE_KEY` | **Generate a private key** → a `.pem` file downloads; its whole content is the value |
| `GITHUB_APP_WEBHOOK_SECRET` | the random string from step 1.3 |
| `PUBLIC_BASE_URL` | `https://journeys.pe` (optional; this is the default) |

## 3. Store them in AWS Secrets Manager

Add the seven keys above to the **same secret** the app already reads (the one named in `AWS_SECRET_NAME` in the server's `.env`). In the AWS console: **Secrets Manager → your secret → Retrieve secret value → Edit → Add row** for each key.

For `GITHUB_APP_PRIVATE_KEY`, paste the full content of the `.pem` file, including the `-----BEGIN` / `-----END` lines. Both real line breaks and `\n` work.

Then delete the downloaded `.pem` file from your computer.

## 4. Restart the backend

```bash
sudo systemctl restart jts-powertool jts-worker
```

## 5. Test it

1. Dashboard → **Clients & Channels → (a test client)**: the **GitHub** card should now show **Connect GitHub**.
2. In a Slack channel that belongs to that client, type `@bot connect my github`. The bot posts a **Connect GitHub** button.
3. Click it → choose a test account/org and one test repository → **Install** → **Authorize**.
4. You land on "GitHub connected", and Slack shows "✅ GitHub connected by @you".
5. Ask the bot something like "list the open issues". It uses the client's GitHub.

## Optional: require every client to connect their own GitHub

By default, a client that hasn't connected GitHub keeps using JTS's own token (`GITHUB_PERSONAL_ACCESS_TOKEN`), so nothing breaks during rollout. Once your clients have connected, add `GITHUB_REQUIRE_CLIENT_CONNECTION` = `true` to the secret. Clients without a connection then get the Connect GitHub button instead of JTS's token.
