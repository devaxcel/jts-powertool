# Plan: One-click GitHub connection + AI website builder

Status: **All phases (0–3) built 2026-10-01; awaiting GitHub App registration and live testing** · Drafted 2026-09-30
Setup guide: [docs/setup/github-app-setup.md](../setup/github-app-setup.md)
Related: *JTS PowerTool Scope of Work v0.1* (sections 4, 6, 8, 9)

## Goals

1. **Connect GitHub from Slack in one click.** A user types `@bot connect my github`, clicks a button, approves on GitHub, and the connection is saved automatically for that customer. Nobody copies or pastes tokens.
2. **Build a complete website from a chat request.** The bot writes the whole site, a human approves once, and the bot creates the repository in the customer's GitHub, publishes it, and replies with the live link. Later edits go through a pull request.

## Decisions made

| Topic | Decision |
|---|---|
| Connection method | **GitHub App** ("JTS PowerTool"), registered once by JTS and installed by each customer |
| Who can connect | **Anyone in the customer's Slack channel** (safeguards below) |
| Model for building websites | **Claude Sonnet 5.5** (`claude-sonnet-5-5`, $2 / $10 per MTok); chat stays on Haiku 4.5 |
| Where new site repos live | **The customer's own GitHub** account/org |
| Security fixes first? | Deferred. Plan first, decide order later (see Phase 0) |

---

## Part A: One-click GitHub connection

### Why a GitHub App

- **No per-customer secrets to paste.** JTS stores **one** app private key in AWS Secrets Manager. For every bot action the server mints a **1-hour installation token** for that customer. This covers Scope §4, "tokens expire and rotate".
- **GitHub enforces repo limits.** The customer picks which repos the app can access, so one customer's token can never touch another customer's repos. This covers Scope §4/§6 isolation.
- **Uninstalling on GitHub disconnects automatically** (via webhook).

### User flow

```
Slack   @bot connect my github
Bot     [ Connect GitHub ]   (link: single-use, expires in 15 min, bound to this channel's customer)
GitHub  "Install JTS PowerTool" → choose account/org + repositories → Install (+ Authorize)
Server  /api/github/callback verifies the link, saves the installation for the customer
Slack   ✅ GitHub connected by @Sara: acme-org (3 repositories)
```

The same **Connect GitHub** button also appears on the customer's page in **Clients & Channels**.

### Safeguards (because anyone in the channel may connect)

- The channel **must belong to a customer**. Unassigned channels get: "Ask JTS to add this channel to a client first."
- The link is **signed, single-use, 15-minute expiry**, and bound to that customer and channel. It can't be reused or forwarded to another customer.
- The bot **announces in the channel** who connected which GitHub account. The customer page shows it too, with a **Disconnect** button (Client Admin / JTS Admin).
- Connecting again **replaces** the previous connection, and the bot announces that too.
- All GitHub *write* actions still need **approval** (unchanged).

### Server pieces

| Piece | Purpose |
|---|---|
| Table `github_connections` | customer (folder_id), installation_id, account login/type, repo selection, connected_by (Slack user), channel, status, timestamps |
| AWS Secrets Manager (app level) | `GITHUB_APP_ID`, `GITHUB_APP_SLUG`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_WEBHOOK_SECRET` |
| AWS Secrets Manager (per customer) | Only the user-authorization **refresh token** (needed to create repos on *personal* accounts); rotated automatically |
| `GET /api/github/connect?state=…` | Checks the signed link, then redirects to GitHub's install page |
| `GET /api/github/callback` | GitHub returns here; saves the connection and posts ✅ in Slack |
| `POST /api/github/webhook` | Signature-verified; handles uninstall / repo-selection changes |
| `GET/DELETE /api/channels/folders/{id}/github` | Status + disconnect for the dashboard |
| Token resolver | customer → installation → fresh 1-hour token (cached ~50 min) |
| Bot tool `connect_github` | Recognises "connect my github" and replies with the button |

Public routes to add to the login-exempt list: `/api/github/connect`, `/api/github/callback`, `/api/github/webhook` (each protected by its own signature/state check).

### Everything uses the customer's connection

- Bot replies, **approvals** (today approvals wrongly use JTS's global token), and the before/after diff all use the customer's installation token.
- A customer **without** a connection: the bot says "Connect GitHub first" with the button. There is **no silent fallback** to JTS's token for customer repos.
- Remove the fake per-channel "GitHub settings" form (it only saved to the browser).

### GitHub App registration (done once by JTS, ~10 min, guided)

- **Homepage:** `https://journeys.pe`
- **Callback URL / Setup URL:** `https://journeys.pe/api/github/callback` (redirect on update ✔)
- **Request user authorization (OAuth) during installation:** ✔ (needed to create repos in personal accounts)
- **Expire user authorization tokens:** ✔
- **Webhook URL:** `https://journeys.pe/api/github/webhook` + a random secret
- **Repository permissions:**
  - Contents: read & write
  - Pull requests: read & write
  - Issues: read & write
  - Administration: read & write (create repos)
  - Pages: read & write
  - Workflows: read & write
  - Metadata: read
- **Events:** Installation, Installation repositories
- **Where can it be installed:** Any account
- The private key and client secret go **directly into AWS Secrets Manager**, never into chat, email or the repo.

---

## Part B: AI website builder

### User flow

```
Client  @bot build a website for my bakery: home, menu, about, contact, warm colours
Bot     Plan: repo "sunrise-bakery-site" · 5 pages · GitHub Pages. Reply "go" or adjust.
Client  go
Bot     writes files into a private DRAFT (nothing on GitHub yet)
        Approval card: "Create website · sunrise-bakery-site · 9 files"  [Preview] [Approve] [Reject]
Admin   Approve
Bot     creates repo → one commit with all files → turns on GitHub Pages
        ✅ Live at https://acme-org.github.io/sunrise-bakery-site (may take ~1 min)
Later   @bot add a gallery page → branch + pull request → approval → merge → site updates
```

### Server pieces

| Piece | Purpose |
|---|---|
| Tables `site_drafts`, `site_draft_files` | Draft per request: customer, channel, repo name, visibility, files (path, content, size), status |
| Tool `draft_write_file(path, content)` | Adds/updates a file in the draft. No GitHub access, no approval needed |
| Tool `draft_list_files` / `draft_read_file` | Lets the bot review its own work |
| Tool `publish_website(repo_name, description, visibility)` | **One approval** for the whole draft → create repo, single commit, enable Pages, return URL |
| Tool `propose_site_change(...)` | For edits: branch + commit + **pull request** (Scope §8: PRs for non-trivial changes) |
| Approve on a PR proposal | Merges the PR (Scope §8: human review before merge) |
| Draft preview endpoint | Dashboard shows the draft site in a sandboxed frame before approval |
| Approvals UI | New card type "Create website" with file list, size, preview button |

### Model and limits

- **Routing:** when the request is a build/website task, use **Sonnet 5.5**; normal chat stays on Haiku 4.5. The price table already has Sonnet 5.5 ($2 / $10).
- **Build mode limits:** more agent steps (e.g. 30 instead of 5) and larger output, with a **cost cap per build** (e.g. $2, configurable), billed to the customer like any reply.
- **Sonnet 5.5 compatibility fixes required:**
  - `app/claude.py:298` forces `tool_choice: {"type": "any"}` on turn 1. **Sonnet 5.5 rejects forced tool choice (400).** Switch to `auto` plus a prompt instruction when using Sonnet 5.5.
  - Don't send `thinking: {"type": "disabled"}` or non-default `temperature/top_p` to Sonnet 5.5 (both are 400s). Current code doesn't, but keep it that way.

### Scope of first version

- **Static sites** (HTML + CSS + a little JS, responsive). No build step, so they're reliable and free on GitHub Pages.
- **Images:** placeholders or free stock links (the bot can't create real photos); customers can upload their own later.
- **Hosting:** GitHub Pages is free for **public** repos. Private-repo sites need a paid GitHub plan on the customer's side.
- **Later:** framework sites (Next.js/Astro) built by GitHub Actions, custom domains, Vercel/Netlify hosting.

### Why not run code like OpenClaw

OpenClaw runs commands on a real computer. For JTS, giving the bot a shell on the server would be a major security risk and would block the HIPAA plan in the Scope of Work. Here **GitHub builds and hosts** the site, and our server only writes files through approved API calls.

---

## Part C: Dashboard changes

- **Customer page:** a GitHub card: connected account, repositories, who connected it and when, **Connect / Disconnect**.
- **Approvals:** the "Create website" card with a file list and **Preview**. PR cards show the diff and "Merge on approve".
- **Websites list** per customer: repo, live URL, last change, status.

---

## Phases and rough effort

| Phase | Work | Effort |
|---|---|---|
| 0 ✅ | Security fixes already identified: fake `X-JTS-Role` admin header, built-in `jts-admin-secret`, clients moving other clients' channels. **Strongly recommended before Phase 1 goes live.** | ~1 day |
| 1 ✅ | GitHub App connection (Part A) + dashboard GitHub card | ~3–4 days |
| 2 ✅ | Website builder: drafts, publish, Pages, preview, Sonnet 5.5 routing (Part B) | ~5–7 days |
| 3 ✅ | Edits via pull requests + merge-on-approve + websites list | ~2–3 days |
| — | JTS registers the GitHub App and adds its keys to Secrets Manager | ~30 min (guided) |

Testing needs a **staging** setup (Scope §9) or at least a test GitHub org and a test Slack channel. Real installs and repo creation can't be tested locally.

## Open questions

1. Default visibility for new site repos: **public** (free Pages) or **private**?
2. Cost cap per website build (suggest $2) and who can raise it.
3. Custom domains (e.g. `www.sunrisebakery.com`) in v1 or later?
4. Should Team Members be allowed to **request** websites, with approval by Client Admin / JTS Admin?
5. When Phase 0 security fixes happen (recommended: before Phase 1 is deployed).
