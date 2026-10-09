# Setup: connect a client's WordPress site (about 5 minutes per site)

Nothing to register on the JTS side. Each client connects their own site with a WordPress **Application Password**.
Never paste the password into Slack, chat or email. It goes only into the dashboard form.

## 1. Create the application password (on the client's site)

1. Sign in to WordPress as the user the bot should act as (an **Editor** is enough for pages and posts; an **Administrator** also works).
2. Open **Users → Profile**, scroll to **Application Passwords**.
3. Name it `JTS PowerTool`, click **Add New Application Password**, and copy the password shown (spaces are fine).
   - The site must be on **HTTPS**. WordPress hides this section on plain HTTP.
   - If the section is missing, a security plugin or host has disabled it; ask them to enable Application Passwords.

## 2. Connect it in the dashboard

1. **Clients & Channels → (the client) → Keys & connections → WordPress → Connect**.
2. Enter the site address (`https://example.com`), the WordPress username and the application password.
3. The page shows the site name, the user, and a count of **editors found** (Gutenberg, Classic, Elementor, Divi) from the 24 most recent pages and posts.

| Editor | What the bot can do |
|---|---|
| Gutenberg (block editor) | read and edit content, create drafts, publish, trash |
| Classic editor | read and edit content, create drafts, publish, trash |
| Elementor | text only (headings, paragraphs, buttons...) and only with the connector plugin below |
| Divi, WPBakery and others | read only |

## 3. Elementor text editing (optional)

Elementor keeps its text in hidden page data, so a small free plugin is needed on the site.

1. On the WordPress card click **Download connector plugin**.
2. In WordPress: **Plugins → Add New → Upload Plugin**, choose the zip, **Install**, **Activate**.
3. Back in the dashboard click **Check again**. The card now shows the plugin version and "Elementor: text editing on".

The plugin only changes text after an approval. It never touches layout, widgets, styles or settings.

## 4. Who can do what

Three new per-user permissions (Users → a user → Permissions): **WordPress read**, **WordPress edit** and **WordPress publish / trash**.
Without publish permission a user can still ask for edits and drafts; going live or trashing needs a user who has it.

## 5. How a change works

1. In Slack: `@bot change the heading on the About page to "Our story"`.
2. The bot reads the page and posts an approval card with a readable **before / after**.
3. An admin approves in Slack or on the Approvals page. Only then is the site changed.
4. If someone edited the same page in the meantime, the change is refused (no overwriting) and the bot re-reads it.
5. WordPress keeps the earlier version under **Revisions**. Trash is not permanent (Trash → Restore).

New pages are always created as **drafts**. Publishing, scheduling and trashing are separate approvals.

## 6. Disconnecting

Dashboard: **Disconnect** removes the stored password. To stop access completely also delete the application password in WordPress (**Users → Profile → Application Passwords → Revoke**).

## Safety limits

- HTTPS only; sites on private or internal addresses are refused.
- The password is stored encrypted, never shown again, never sent to the AI.
- No plugin, theme, user or settings changes: pages and posts only.
