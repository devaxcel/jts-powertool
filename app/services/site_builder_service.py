"""
AI website builder.

The bot writes a site file-by-file into a private DRAFT (database only, nothing on GitHub). When it's ready the bot
proposes `publish_website`; after ONE human approval we create the repository in the client's own GitHub, commit all
files in a single commit, turn on GitHub Pages and return the live URL.
"""
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import mimetypes
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.db.session import get_db_connection
from app.services import github_app_service as gh
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)

MAX_FILES = 60
MAX_FILE_BYTES = 250_000
MAX_TOTAL_BYTES = 3_000_000
PREVIEW_TTL_SECONDS = 24 * 3600
ALLOWED_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg", ".txt", ".md", ".xml", ".webmanifest", ".map",
}
ALLOWED_BARE_NAMES = {"CNAME", ".nojekyll", "LICENSE"}
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

BUILD_INTENT_PATTERNS = [
    r"\b(build|create|make|design|generate|develop|set ?up)\b.{0,60}\b(web ?site|site|landing ?page|web ?page|homepage|portfolio)\b",
    r"\b(web ?site|landing ?page)\b.{0,40}\b(for (me|my|our|us)|builder)\b",
    # editing a site the bot already published
    r"\b(update|change|edit|modify|add|remove|replace|redesign|tweak)\b.{0,60}\b(web ?site|site|landing ?page|homepage|home page)\b",
    r"\b(web ?site|site)\b.{0,40}\b(colou?rs?|logo|menu|footer|header|gallery|contact (page|form)|pricing)\b",
]

# Extra instructions added to the system prompt in build mode.
BUILD_MODE_INSTRUCTIONS = """
[WEBSITE BUILDER MODE]
You can build complete static websites (HTML, CSS, a little JavaScript) and publish them to the client's own GitHub
with GitHub Pages hosting.

How to work:
1. If the request is vague, ask at most 2 short questions (business name, pages, style/colours). Otherwise propose a
   short plan (repo name in lowercase-with-dashes, list of pages) and start when the user agrees, or right away if they
   said "go"/gave enough detail.
2. Write every file with draft_write_file, one file per call, complete content, no placeholders like "...rest here".
   Always include index.html at the root. Use relative links (about.html, styles.css), never absolute "/" paths, so
   the site works under https://<owner>.github.io/<repo>/.
3. Make it responsive (mobile first), accessible (alt text, labels, contrast), fast (no build step, no frameworks
   that need compiling). You may load fonts from Google Fonts. For images use free placeholders such as
   https://images.unsplash.com/... or https://picsum.photos/..., and tell the user they can send real photos later.
4. Use draft_list_files to check your work before publishing.
5. When the site is complete, call publish_website once. A human must approve it; tell the user that an approval card
   with a preview link was posted. Never claim the site is live before approval.
6. To change a site that's already published: call list_my_websites if unsure which one, then start_site_edit
   with its repository name. That loads the live files into the draft. Change only what's needed with
   draft_write_file / draft_delete_file, then call propose_site_changes with a short title and summary. A human
   approves; the change is applied as a pull request that is merged automatically. Never use publish_website for edits.
Never ask for GitHub tokens or passwords. If GitHub isn't connected for this client, call connect_github.
""".strip()


class SiteBuilderError(Exception):
    """A problem we can explain to the user or the bot in plain words."""


# --------------------------------------------------------------------------- tables / db

def _ensure_tables(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS site_drafts (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER,
            channel_id VARCHAR(255) NOT NULL,
            thread_ts VARCHAR(255) NOT NULL,
            created_by VARCHAR(255),
            status VARCHAR(30) DEFAULT 'drafting',
            repo_full_name VARCHAR(255),
            site_url TEXT,
            approval_id VARCHAR(64),
            last_error TEXT,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_site_drafts_thread ON site_drafts (channel_id, thread_ts, status);
        CREATE TABLE IF NOT EXISTS site_draft_files (
            draft_id INTEGER NOT NULL REFERENCES site_drafts(id) ON DELETE CASCADE,
            path VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (draft_id, path)
        );
        -- Phase 3: editing published sites
        ALTER TABLE site_drafts ADD COLUMN IF NOT EXISTS kind VARCHAR(20) DEFAULT 'create';
        ALTER TABLE site_drafts ADD COLUMN IF NOT EXISTS base_repo VARCHAR(255);
        ALTER TABLE site_drafts ADD COLUMN IF NOT EXISTS base_branch VARCHAR(255);
        ALTER TABLE site_drafts ADD COLUMN IF NOT EXISTS base_sha VARCHAR(64);
        CREATE TABLE IF NOT EXISTS site_draft_base_files (
            draft_id INTEGER NOT NULL REFERENCES site_drafts(id) ON DELETE CASCADE,
            path VARCHAR(255) NOT NULL,
            content TEXT NOT NULL,
            PRIMARY KEY (draft_id, path)
        );
        CREATE TABLE IF NOT EXISTS client_websites (
            id SERIAL PRIMARY KEY,
            folder_id INTEGER,
            repo_full_name VARCHAR(255) UNIQUE NOT NULL,
            site_url TEXT,
            default_branch VARCHAR(255) DEFAULT 'main',
            last_change TEXT,
            last_pr_url TEXT,
            created_by VARCHAR(255),
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_client_websites_folder ON client_websites (folder_id);
    """)


def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure_tables(cur)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


OPEN_STATUSES = ("drafting", "pending_approval")


def get_open_draft(channel_id: str, thread_ts: str) -> Optional[Dict[str, Any]]:
    if not channel_id or not thread_ts:
        return None

    def run(cur):
        cur.execute(
            """
            SELECT * FROM site_drafts
            WHERE channel_id = %s AND thread_ts = %s AND status IN ('drafting', 'pending_approval')
              AND updated_at > CURRENT_TIMESTAMP - INTERVAL '7 days'
            ORDER BY id DESC LIMIT 1;
            """,
            (channel_id, thread_ts),
        )
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def get_or_create_draft(channel_id: str, thread_ts: str, folder_id: Optional[int], created_by: Optional[str]) -> Dict[str, Any]:
    draft = get_open_draft(channel_id, thread_ts)
    if draft:
        if draft["status"] == "pending_approval":
            # Editing after proposing: the old proposal no longer matches; go back to drafting.
            set_status(draft["id"], "drafting")
            draft["status"] = "drafting"
        return draft

    def run(cur):
        cur.execute(
            "INSERT INTO site_drafts (folder_id, channel_id, thread_ts, created_by) VALUES (%s, %s, %s, %s) RETURNING *;",
            (folder_id, channel_id, thread_ts, created_by),
        )
        return cur.fetchone()

    return dict(_db(run))


def get_draft(draft_id: int) -> Optional[Dict[str, Any]]:
    def run(cur):
        cur.execute("SELECT * FROM site_drafts WHERE id = %s;", (draft_id,))
        return cur.fetchone()

    row = _db(run)
    return dict(row) if row else None


def set_status(draft_id: int, status: str, **fields) -> None:
    sets = ["status = %s", "updated_at = CURRENT_TIMESTAMP"]
    params: List[Any] = [status]
    for k in ("repo_full_name", "site_url", "approval_id", "last_error"):
        if k in fields:
            sets.append(f"{k} = %s")
            params.append(fields[k])
    params.append(draft_id)

    def run(cur):
        cur.execute(f"UPDATE site_drafts SET {', '.join(sets)} WHERE id = %s;", params)

    _db(run)


def has_open_draft(channel_id: str, thread_ts: str) -> bool:
    try:
        return get_open_draft(channel_id, thread_ts) is not None
    except Exception as e:
        logger.debug(f"[SITE_BUILDER] draft lookup failed: {e}")
        return False


def detect_build_intent(text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in BUILD_INTENT_PATTERNS)


# --------------------------------------------------------------------------- files

def normalize_path(path: str) -> str:
    p = (path or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or len(p) > 200:
        raise SiteBuilderError("Use a relative file path like 'index.html' or 'css/styles.css'.")
    parts = p.split("/")
    for seg in parts:
        if seg in ("", ".", "..") or seg.lower() == ".git" or (not _SEGMENT_RE.match(seg) and seg not in ALLOWED_BARE_NAMES):
            raise SiteBuilderError(f"'{path}' isn't an allowed file path (letters, numbers, . _ - and folders only).")
    name = parts[-1]
    ext = ("." + name.rsplit(".", 1)[1].lower()) if "." in name.lstrip(".") else ""
    if name not in ALLOWED_BARE_NAMES and ext not in ALLOWED_EXTENSIONS:
        raise SiteBuilderError(
            f"'{name}' isn't supported. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}. "
            "Images must be linked from the web for now (binary files can't be written)."
        )
    return p


def write_file(draft_id: int, path: str, content: str) -> Dict[str, Any]:
    path = normalize_path(path)
    if content is None:
        raise SiteBuilderError("File content is missing.")
    size = len(content.encode("utf-8"))
    if size > MAX_FILE_BYTES:
        raise SiteBuilderError(f"'{path}' is {size // 1000} KB; the limit is {MAX_FILE_BYTES // 1000} KB per file. Split it up.")

    def run(cur):
        cur.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS total FROM site_draft_files WHERE draft_id = %s AND path <> %s;",
            (draft_id, path),
        )
        row = cur.fetchone()
        if int(row["n"]) + 1 > MAX_FILES:
            raise SiteBuilderError(f"A site can have at most {MAX_FILES} files.")
        if int(row["total"]) + size > MAX_TOTAL_BYTES:
            raise SiteBuilderError(f"The site would exceed {MAX_TOTAL_BYTES // 1_000_000} MB in total.")
        cur.execute(
            """
            INSERT INTO site_draft_files (draft_id, path, content, size_bytes) VALUES (%s, %s, %s, %s)
            ON CONFLICT (draft_id, path) DO UPDATE SET content = EXCLUDED.content, size_bytes = EXCLUDED.size_bytes,
                                                       updated_at = CURRENT_TIMESTAMP;
            """,
            (draft_id, path, content, size),
        )
        cur.execute("UPDATE site_drafts SET updated_at = CURRENT_TIMESTAMP WHERE id = %s;", (draft_id,))
        cur.execute("SELECT COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS total FROM site_draft_files WHERE draft_id = %s;", (draft_id,))
        return cur.fetchone()

    totals = _db(run)
    return {"path": path, "size_bytes": size, "file_count": int(totals["n"]), "total_bytes": int(totals["total"])}


def list_files(draft_id: int) -> List[Dict[str, Any]]:
    def run(cur):
        cur.execute("SELECT path, size_bytes FROM site_draft_files WHERE draft_id = %s ORDER BY path;", (draft_id,))
        return cur.fetchall() or []

    return [{"path": r["path"], "size_bytes": int(r["size_bytes"])} for r in _db(run)]


def read_file(draft_id: int, path: str) -> Optional[str]:
    path = normalize_path(path)

    def run(cur):
        cur.execute("SELECT content FROM site_draft_files WHERE draft_id = %s AND path = %s;", (draft_id, path))
        return cur.fetchone()

    row = _db(run)
    return row["content"] if row else None


def delete_file(draft_id: int, path: str) -> bool:
    path = normalize_path(path)

    def run(cur):
        cur.execute("DELETE FROM site_draft_files WHERE draft_id = %s AND path = %s RETURNING path;", (draft_id, path))
        return cur.fetchone()

    return bool(_db(run))


def _all_files(draft_id: int) -> List[Dict[str, Any]]:
    def run(cur):
        cur.execute("SELECT path, content FROM site_draft_files WHERE draft_id = %s ORDER BY path;", (draft_id,))
        return cur.fetchall() or []

    return [dict(r) for r in _db(run)]


# --------------------------------------------------------------------------- preview links

def _preview_secret() -> bytes:
    from app.auth_router import _SECRET_KEY

    extra = (get_secret("SITE_PREVIEW_SECRET", "") or "").encode()
    return hashlib.sha256((extra or b"site-preview") + b"|" + (_SECRET_KEY if isinstance(_SECRET_KEY, bytes) else str(_SECRET_KEY).encode())).digest()


def create_preview_token(draft_id: int, ttl: int = PREVIEW_TTL_SECONDS) -> str:
    payload = f"{int(draft_id)}.{int(time.time()) + ttl}"
    sig = hmac.new(_preview_secret(), payload.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=") + "." + base64.urlsafe_b64encode(sig).decode().rstrip("=")


def verify_preview_token(token: str) -> Optional[int]:
    try:
        b64_payload, b64_sig = token.split(".", 1)
        payload = base64.urlsafe_b64decode(b64_payload + "=" * (-len(b64_payload) % 4)).decode()
        sig = base64.urlsafe_b64decode(b64_sig + "=" * (-len(b64_sig) % 4))
        expected = hmac.new(_preview_secret(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(sig, expected):
            return None
        draft_id, exp = payload.split(".", 1)
        if int(exp) < time.time():
            return None
        return int(draft_id)
    except Exception:
        return None


def preview_url(draft_id: int) -> str:
    return f"{gh.public_base_url()}/api/site-preview/{create_preview_token(draft_id)}/index.html"


def preview_file(token: str, path: str) -> Tuple[str, str]:
    draft_id = verify_preview_token(token)
    if not draft_id:
        raise SiteBuilderError("This preview link has expired.")
    path = path or "index.html"
    if path.endswith("/"):
        path += "index.html"
    content = read_file(draft_id, path)
    if content is None and "." not in path.rsplit("/", 1)[-1]:
        content = read_file(draft_id, path.rstrip("/") + "/index.html")
    if content is None:
        raise SiteBuilderError("File not found in this draft.")
    mime = mimetypes.guess_type(path)[0] or "text/plain"
    return content, mime


# --------------------------------------------------------------------------- proposal

def build_publish_proposal(draft: Dict[str, Any], repo_name: str, description: str, private: bool) -> Dict[str, Any]:
    if (draft.get("kind") or "create") == "edit":
        raise SiteBuilderError("This draft edits an existing website. Use propose_site_changes instead of publish_website.")
    if not REPO_NAME_RE.match(repo_name or ""):
        raise SiteBuilderError("Repository names may only use letters, numbers, '-', '_' and '.', up to 100 characters.")
    files = list_files(draft["id"])
    if not files:
        raise SiteBuilderError("The draft is empty. Write the site files with draft_write_file first.")
    if not any(f["path"] == "index.html" for f in files):
        raise SiteBuilderError("The site needs an index.html at the root before it can be published.")
    conn = gh.get_connection(draft.get("folder_id"))
    if not conn or conn.get("status") != "active":
        raise SiteBuilderError("This client hasn't connected GitHub yet. Call connect_github first.")
    return {
        "draft_id": draft["id"],
        "owner": conn["account_login"],
        "owner_type": conn.get("account_type"),
        "repo": repo_name,
        "description": (description or "")[:300],
        "private": bool(private),
        "files": files,
        "file_count": len(files),
        "total_bytes": sum(f["size_bytes"] for f in files),
        "preview_url": preview_url(draft["id"]),
    }


# --------------------------------------------------------------------------- publish (after approval)

async def _user_access_token(folder_id: int) -> str:
    """Refreshes the stored GitHub user token (refresh tokens rotate, so the new one is saved back)."""
    from app.services.channel_secrets_service import get_folder_api_key_value, store_folder_api_key

    raw = get_folder_api_key_value(folder_id, gh.USER_TOKEN_PROVIDER)
    if not raw:
        raise gh.GitHubAppError("GitHub needs to be reconnected for this client (no authorization saved).")
    stored = json.loads(raw)
    cfg = gh.app_config()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{gh.GITHUB_WEB}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            json={
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": stored.get("refresh_token"),
            },
        )
    data = resp.json() if resp.content else {}
    if resp.status_code != 200 or not data.get("access_token"):
        raise gh.GitHubAppError("GitHub's authorization for this client expired. Please reconnect GitHub.")
    if data.get("refresh_token"):
        stored.update(refresh_token=data["refresh_token"], saved_at=int(time.time()))
        try:
            store_folder_api_key(folder_id=folder_id, api_key=json.dumps(stored), provider=gh.USER_TOKEN_PROVIDER, updated_by="github-refresh")
        except Exception as e:
            logger.warning(f"[SITE_BUILDER] Could not save rotated GitHub refresh token for folder {folder_id}: {e}")
    return data["access_token"]


async def _gh_call(client: httpx.AsyncClient, method: str, path: str, token: str, **kw) -> httpx.Response:
    return await client.request(method, f"{gh.GITHUB_API}{path}", headers=gh._headers(token), **kw)


def _err(resp: httpx.Response) -> str:
    try:
        data = resp.json()
        msg = data.get("message", "")
        details = "; ".join(str(e.get("message") or e.get("code") or e) for e in data.get("errors", []) if e)
        return f"{msg}{(': ' + details) if details else ''}"
    except Exception:
        return resp.text[:200]


async def publish_draft(args: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    """Runs after approval. Returns {"repo", "url", "pages", "files"}; raises SiteBuilderError / GitHubAppError."""
    draft = get_draft(int(args["draft_id"]))
    if not draft:
        raise SiteBuilderError("The draft for this website no longer exists.")
    if draft["status"] == "published":
        raise SiteBuilderError(f"This website was already published at {draft.get('site_url')}.")
    from app.services.channel_secrets_service import get_folder_id_for_channel

    folder_id = draft.get("folder_id") or get_folder_id_for_channel(channel_id)
    conn = gh.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise gh.GitHubAppError("This client's GitHub isn't connected. Reconnect GitHub, then ask the bot to publish again.")
    if conn["account_login"] != args.get("owner"):
        raise SiteBuilderError("The client's GitHub connection changed since this was proposed. Ask the bot to propose publishing again.")

    files = _all_files(draft["id"])
    if not any(f["path"] == "index.html" for f in files):
        raise SiteBuilderError("The draft has no index.html.")
    set_status(draft["id"], "publishing")
    owner, repo = conn["account_login"], args["repo"]
    token = await _user_access_token(folder_id)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # 1. Create the repository in the client's own account/org
            body = {"name": repo, "description": args.get("description") or "Website built with JTS PowerTool",
                    "private": bool(args.get("private")), "auto_init": True, "has_wiki": False}
            path = f"/orgs/{owner}/repos" if conn.get("account_type") == "Organization" else "/user/repos"
            resp = await _gh_call(client, "POST", path, token, json=body)
            if resp.status_code == 422 and "already exists" in resp.text:
                raise SiteBuilderError(f"A repository named '{repo}' already exists in {owner}. Pick another name and publish again.")
            if resp.status_code not in (200, 201):
                raise SiteBuilderError(f"GitHub didn't create the repository ({resp.status_code}: {_err(resp)}).")
            created = resp.json()
            full_name = created["full_name"]
            branch = created.get("default_branch") or "main"

            # 2. Wait for the initial commit, then add every file in ONE commit
            base_sha = None
            for _ in range(8):
                ref = await _gh_call(client, "GET", f"/repos/{full_name}/git/ref/heads/{branch}", token)
                if ref.status_code == 200:
                    base_sha = ref.json()["object"]["sha"]
                    break
                await asyncio.sleep(1.5)
            if not base_sha:
                raise SiteBuilderError(
                    "The repository was created but JTS PowerTool can't write to it. If the GitHub app is limited to "
                    f"selected repositories, add '{full_name}' to it (or allow all repositories) and publish again."
                )
            base_commit = await _gh_call(client, "GET", f"/repos/{full_name}/git/commits/{base_sha}", token)
            tree_items = [{"path": f["path"], "mode": "100644", "type": "blob", "content": f["content"]} for f in files]
            if not any(f["path"] == ".nojekyll" for f in files):
                tree_items.append({"path": ".nojekyll", "mode": "100644", "type": "blob", "content": "\n"})
            tree = await _gh_call(client, "POST", f"/repos/{full_name}/git/trees", token,
                                  json={"base_tree": base_commit.json()["tree"]["sha"], "tree": tree_items})
            if tree.status_code != 201:
                raise SiteBuilderError(f"GitHub rejected the files ({tree.status_code}: {_err(tree)}).")
            commit = await _gh_call(client, "POST", f"/repos/{full_name}/git/commits", token, json={
                "message": f"Publish website ({len(files)} files) via JTS PowerTool",
                "tree": tree.json()["sha"], "parents": [base_sha],
            })
            if commit.status_code != 201:
                raise SiteBuilderError(f"GitHub didn't save the commit ({commit.status_code}: {_err(commit)}).")
            upd = await _gh_call(client, "PATCH", f"/repos/{full_name}/git/refs/heads/{branch}", token,
                                 json={"sha": commit.json()["sha"]})
            if upd.status_code != 200:
                raise SiteBuilderError(f"GitHub didn't update the branch ({upd.status_code}: {_err(upd)}).")

            # 3. Turn on GitHub Pages
            pages_ok, site_url, pages_note = True, f"https://{owner.lower()}.github.io/{repo}/", ""
            pages = await _gh_call(client, "POST", f"/repos/{full_name}/pages", token,
                                   json={"source": {"branch": branch, "path": "/"}})
            if pages.status_code in (201, 409):
                if pages.status_code == 201:
                    site_url = pages.json().get("html_url") or site_url
            else:
                pages_ok = False
                pages_note = (
                    "GitHub Pages couldn't be turned on"
                    + (" (private repositories need a paid GitHub plan for Pages)" if args.get("private") else "")
                    + f": {_err(pages)}"
                )
    except (SiteBuilderError, gh.GitHubAppError) as e:
        set_status(draft["id"], "failed", last_error=str(e))
        raise
    except Exception as e:
        set_status(draft["id"], "failed", last_error=str(e))
        logger.error(f"[SITE_BUILDER] Publish failed for draft {draft['id']}: {e}", exc_info=True)
        raise SiteBuilderError("Publishing failed because GitHub didn't respond as expected. Please try again.")

    set_status(draft["id"], "published", repo_full_name=full_name, site_url=site_url if pages_ok else None)
    record_website(folder_id, full_name, site_url if pages_ok else None, branch, "First version published", None, draft.get("created_by"))
    logger.info(f"[SITE_BUILDER] Draft {draft['id']} published to {full_name} ({len(files)} files) pages={pages_ok}")
    return {"repo": full_name, "url": site_url if pages_ok else None, "pages": pages_ok, "pages_note": pages_note,
            "files": len(files), "repo_url": f"{gh.GITHUB_WEB}/{full_name}"}


async def execute_publish_approval(args: Dict[str, Any], channel_id: str) -> Tuple[str, bool]:
    """Approval executor used by the web dashboard and Slack buttons. Returns (output, is_error)."""
    try:
        res = await publish_draft(args, channel_id)
    except (SiteBuilderError, gh.GitHubAppError) as e:
        return f"[Publish Error]: {e}", True
    if res["pages"]:
        return (
            f"Website published. Live at {res['url']} (GitHub Pages can take about a minute to show the first time). "
            f"Repository: {res['repo_url']} ({res['files']} files).", False,
        )
    return f"Repository created at {res['repo_url']} ({res['files']} files), but {res['pages_note']}", False


def check_can_publish(channel_id: str) -> None:
    """Pre-approval check so a missing connection doesn't consume the approval."""
    from app.services.channel_secrets_service import get_folder_id_for_channel

    conn = gh.get_connection(get_folder_id_for_channel(channel_id))
    if not conn or conn.get("status") != "active":
        raise gh.GitHubAppError("This client's GitHub isn't connected. Ask the bot to “connect my github” first.")
    if not gh.is_configured():
        raise gh.GitHubAppError("GitHub isn't set up on JTS PowerTool yet.")


# =========================================================================== Phase 3: websites list + edits

def record_website(
    folder_id: Optional[int], repo_full_name: str, site_url: Optional[str], default_branch: str,
    last_change: Optional[str], last_pr_url: Optional[str], created_by: Optional[str],
) -> None:
    def run(cur):
        cur.execute(
            """
            INSERT INTO client_websites (folder_id, repo_full_name, site_url, default_branch, last_change, last_pr_url, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (repo_full_name) DO UPDATE SET
                site_url = COALESCE(EXCLUDED.site_url, client_websites.site_url),
                default_branch = EXCLUDED.default_branch,
                last_change = EXCLUDED.last_change,
                last_pr_url = COALESCE(EXCLUDED.last_pr_url, client_websites.last_pr_url),
                updated_at = CURRENT_TIMESTAMP;
            """,
            (folder_id, repo_full_name, site_url, default_branch, last_change, last_pr_url, created_by),
        )

    try:
        _db(run)
    except Exception as e:
        logger.warning(f"[SITE_BUILDER] Could not record website {repo_full_name}: {e}")


def list_websites(folder_id: Optional[int]) -> List[Dict[str, Any]]:
    def run(cur):
        if folder_id is None:
            cur.execute("SELECT * FROM client_websites ORDER BY updated_at DESC;")
        else:
            cur.execute("SELECT * FROM client_websites WHERE folder_id = %s ORDER BY updated_at DESC;", (folder_id,))
        return cur.fetchall() or []

    out = []
    for r in _db(run):
        rec = dict(r)
        for k in ("created_at", "updated_at"):
            if rec.get(k) is not None and hasattr(rec[k], "isoformat"):
                rec[k] = rec[k].isoformat()
        out.append(rec)
    return out


def find_website(folder_id: int, name: str) -> Optional[Dict[str, Any]]:
    """Match by full name ('owner/repo') or just the repo name, within this client's websites only."""
    wanted = (name or "").strip().strip("/").lower()
    for w in list_websites(folder_id):
        full = w["repo_full_name"].lower()
        if wanted in (full, full.split("/", 1)[1]):
            return w
    return None


def _set_base_files(draft_id: int, files: List[Dict[str, str]]) -> None:
    def run(cur):
        cur.execute("DELETE FROM site_draft_base_files WHERE draft_id = %s;", (draft_id,))
        cur.execute("DELETE FROM site_draft_files WHERE draft_id = %s;", (draft_id,))
        for f in files:
            size = len(f["content"].encode("utf-8"))
            cur.execute("INSERT INTO site_draft_base_files (draft_id, path, content) VALUES (%s, %s, %s);", (draft_id, f["path"], f["content"]))
            cur.execute(
                "INSERT INTO site_draft_files (draft_id, path, content, size_bytes) VALUES (%s, %s, %s, %s);",
                (draft_id, f["path"], f["content"], size),
            )

    _db(run)


def _base_files(draft_id: int) -> Dict[str, str]:
    def run(cur):
        cur.execute("SELECT path, content FROM site_draft_base_files WHERE draft_id = %s;", (draft_id,))
        return cur.fetchall() or []

    return {r["path"]: r["content"] for r in _db(run)}


def compute_changes(draft_id: int) -> Dict[str, List[str]]:
    base = _base_files(draft_id)
    current = {f["path"]: f["content"] for f in _all_files(draft_id)}
    return {
        "added": sorted(p for p in current if p not in base),
        "modified": sorted(p for p in current if p in base and current[p] != base[p]),
        "deleted": sorted(p for p in base if p not in current),
    }


def update_diff_text(draft_id: int) -> str:
    """Unified diff of the proposed edit, for the Approvals page."""
    import difflib

    base = _base_files(draft_id)
    current = {f["path"]: f["content"] for f in _all_files(draft_id)}
    chunks = []
    for path in sorted(set(base) | set(current)):
        old, new = base.get(path), current.get(path)
        if old == new:
            continue
        diff = difflib.unified_diff(
            (old or "").splitlines(), (new or "").splitlines(),
            fromfile=f"a/{path}" if old is not None else "/dev/null",
            tofile=f"b/{path}" if new is not None else "/dev/null",
            lineterm="", n=3,
        )
        chunks.append("\n".join(diff))
    return "\n\n".join(chunks) or "(no changes)"


async def start_edit(channel_id: str, thread_ts: str, folder_id: int, created_by: Optional[str], repo: str) -> Dict[str, Any]:
    """Opens an edit draft pre-filled with the site's current files from GitHub."""
    site = find_website(folder_id, repo)
    if not site:
        names = ", ".join(w["repo_full_name"] for w in list_websites(folder_id)) or "none yet"
        raise SiteBuilderError(f"'{repo}' isn't one of this client's websites (known: {names}).")

    existing = get_open_draft(channel_id, thread_ts)
    if existing:
        if (existing.get("kind") == "edit") and (existing.get("base_repo") == site["repo_full_name"]):
            return {"draft_id": existing["id"], "repo": site["repo_full_name"], "reused": True, "files": list_files(existing["id"]), "skipped": []}
        set_status(existing["id"], "discarded")

    conn = gh.get_connection(folder_id)
    if not conn or conn.get("status") != "active":
        raise gh.GitHubAppError("This client's GitHub isn't connected. Call connect_github first.")
    token = await _user_access_token(folder_id)
    full, branch = site["repo_full_name"], site.get("default_branch") or "main"

    files: List[Dict[str, str]] = []
    skipped: List[str] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        ref = await _gh_call(client, "GET", f"/repos/{full}/git/ref/heads/{branch}", token)
        if ref.status_code != 200:
            raise SiteBuilderError(f"Couldn't read {full} on GitHub ({ref.status_code}: {_err(ref)}).")
        head_sha = ref.json()["object"]["sha"]
        tree = await _gh_call(client, "GET", f"/repos/{full}/git/trees/{head_sha}", token, params={"recursive": "1"})
        if tree.status_code != 200:
            raise SiteBuilderError(f"Couldn't list the files of {full} ({tree.status_code}).")
        total = 0
        for item in tree.json().get("tree", []):
            if item.get("type") != "blob":
                continue
            path = item["path"]
            size = int(item.get("size") or 0)
            try:
                normalize_path(path)
            except SiteBuilderError:
                skipped.append(path)  # images and other non-text files stay untouched in the repository
                continue
            if size > MAX_FILE_BYTES or len(files) >= MAX_FILES or total + size > MAX_TOTAL_BYTES:
                skipped.append(path)
                continue
            blob = await _gh_call(client, "GET", f"/repos/{full}/git/blobs/{item['sha']}", token)
            if blob.status_code != 200:
                skipped.append(path)
                continue
            try:
                content = base64.b64decode(blob.json().get("content", "")).decode("utf-8")
            except Exception:
                skipped.append(path)
                continue
            files.append({"path": path, "content": content})
            total += len(content.encode("utf-8"))

    def run(cur):
        cur.execute(
            """
            INSERT INTO site_drafts (folder_id, channel_id, thread_ts, created_by, kind, base_repo, base_branch, base_sha)
            VALUES (%s, %s, %s, %s, 'edit', %s, %s, %s) RETURNING *;
            """,
            (folder_id, channel_id, thread_ts, created_by, full, branch, head_sha),
        )
        return cur.fetchone()

    draft = dict(_db(run))
    _set_base_files(draft["id"], files)
    return {"draft_id": draft["id"], "repo": full, "reused": False, "files": list_files(draft["id"]), "skipped": skipped}


def build_update_proposal(draft: Dict[str, Any], title: str, summary: str) -> Dict[str, Any]:
    if (draft.get("kind") or "create") != "edit":
        raise SiteBuilderError("This draft is a new website. Use publish_website for it.")
    title = (title or "").strip()[:120]
    if not title:
        raise SiteBuilderError("Give the change a short title, e.g. 'Add gallery page'.")
    changes = compute_changes(draft["id"])
    n = sum(len(v) for v in changes.values())
    if not n:
        raise SiteBuilderError("No files differ from the live site yet. Make the edits with draft_write_file first.")
    if not any(f["path"] == "index.html" for f in list_files(draft["id"])):
        raise SiteBuilderError("The site must keep an index.html.")
    conn = gh.get_connection(draft.get("folder_id"))
    if not conn or conn.get("status") != "active":
        raise SiteBuilderError("This client hasn't connected GitHub. Call connect_github first.")
    owner, repo = draft["base_repo"].split("/", 1)
    return {
        "draft_id": draft["id"],
        "owner": owner,
        "repo": repo,
        "base_branch": draft.get("base_branch") or "main",
        "title": title,
        "summary": (summary or "").strip()[:2000],
        "changes": changes,
        "change_count": n,
        "preview_url": preview_url(draft["id"]),
    }


async def apply_update(args: Dict[str, Any], channel_id: str, approved_by: Optional[str]) -> Dict[str, Any]:
    """After approval: branch -> commit -> pull request -> merge (squash). Pages redeploys from the default branch."""
    draft = get_draft(int(args["draft_id"]))
    if not draft or (draft.get("kind") != "edit"):
        raise SiteBuilderError("The edit draft for this change no longer exists.")
    if draft["status"] == "published":
        raise SiteBuilderError("This change was already applied.")
    folder_id = draft["folder_id"]
    full, base_branch, base_sha = draft["base_repo"], draft.get("base_branch") or "main", draft["base_sha"]
    if f"{args.get('owner')}/{args.get('repo')}" != full:
        raise SiteBuilderError("The proposal doesn't match its draft. Ask the bot to propose the changes again.")

    changes = compute_changes(draft["id"])
    changed_paths = set(changes["added"]) | set(changes["modified"]) | set(changes["deleted"])
    if not changed_paths:
        raise SiteBuilderError("There are no changes to apply.")
    current = {f["path"]: f["content"] for f in _all_files(draft["id"])}
    token = await _user_access_token(folder_id)
    set_status(draft["id"], "publishing")
    branch = f"jts/update-{draft['id']}-{int(time.time())}"
    title = args.get("title") or "Website update"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            ref = await _gh_call(client, "GET", f"/repos/{full}/git/ref/heads/{base_branch}", token)
            if ref.status_code != 200:
                raise SiteBuilderError(f"Couldn't read {full} ({ref.status_code}: {_err(ref)}).")
            head_sha = ref.json()["object"]["sha"]

            # Someone else changed the same files since the draft was loaded -> don't overwrite their work.
            if head_sha != base_sha:
                cmp = await _gh_call(client, "GET", f"/repos/{full}/compare/{base_sha}...{head_sha}", token)
                touched = {f["filename"] for f in (cmp.json().get("files") or [])} if cmp.status_code == 200 else None
                if touched is None or touched & changed_paths:
                    raise SiteBuilderError(
                        "The website was changed on GitHub after this edit started, in the same files. Ask the bot to "
                        "start the edit again so it works from the latest version."
                    )

            head_commit = await _gh_call(client, "GET", f"/repos/{full}/git/commits/{head_sha}", token)
            tree_items = [{"path": p, "mode": "100644", "type": "blob", "content": current[p]}
                          for p in sorted(set(changes["added"]) | set(changes["modified"]))]
            tree_items += [{"path": p, "mode": "100644", "type": "blob", "sha": None} for p in changes["deleted"]]
            tree = await _gh_call(client, "POST", f"/repos/{full}/git/trees", token,
                                  json={"base_tree": head_commit.json()["tree"]["sha"], "tree": tree_items})
            if tree.status_code != 201:
                raise SiteBuilderError(f"GitHub rejected the changes ({tree.status_code}: {_err(tree)}).")
            commit = await _gh_call(client, "POST", f"/repos/{full}/git/commits", token,
                                    json={"message": title, "tree": tree.json()["sha"], "parents": [head_sha]})
            if commit.status_code != 201:
                raise SiteBuilderError(f"GitHub didn't save the commit ({commit.status_code}: {_err(commit)}).")
            new_ref = await _gh_call(client, "POST", f"/repos/{full}/git/refs", token,
                                     json={"ref": f"refs/heads/{branch}", "sha": commit.json()["sha"]})
            if new_ref.status_code != 201:
                raise SiteBuilderError(f"GitHub didn't create the branch ({new_ref.status_code}: {_err(new_ref)}).")

            body_lines = [args.get("summary") or "", "", "**Files**"]
            body_lines += [f"- added `{p}`" for p in changes["added"]]
            body_lines += [f"- changed `{p}`" for p in changes["modified"]]
            body_lines += [f"- removed `{p}`" for p in changes["deleted"]]
            body_lines += ["", f"Proposed by the JTS PowerTool assistant; reviewed and approved by {approved_by or 'an admin'}."]
            pr = await _gh_call(client, "POST", f"/repos/{full}/pulls", token, json={
                "title": title, "head": branch, "base": base_branch, "body": "\n".join(body_lines).strip(),
            })
            if pr.status_code != 201:
                raise SiteBuilderError(f"GitHub didn't open the pull request ({pr.status_code}: {_err(pr)}).")
            pr_data = pr.json()
            pr_url, pr_number = pr_data["html_url"], pr_data["number"]

            merge = await _gh_call(client, "PUT", f"/repos/{full}/pulls/{pr_number}/merge", token,
                                   json={"merge_method": "squash", "commit_title": f"{title} (#{pr_number})"})
            merged = merge.status_code == 200 and bool(merge.json().get("merged"))
            if merged:
                await _gh_call(client, "DELETE", f"/repos/{full}/git/refs/heads/{branch}", token)
    except (SiteBuilderError, gh.GitHubAppError) as e:
        set_status(draft["id"], "failed", last_error=str(e))
        raise
    except Exception as e:
        set_status(draft["id"], "failed", last_error=str(e))
        logger.error(f"[SITE_BUILDER] Update failed for draft {draft['id']}: {e}", exc_info=True)
        raise SiteBuilderError("Updating the website failed because GitHub didn't respond as expected. Please try again.")

    site = find_website(folder_id, full) or {}
    set_status(draft["id"], "published", repo_full_name=full, site_url=site.get("site_url"))
    record_website(folder_id, full, None, base_branch, title, pr_url, approved_by)
    return {"repo": full, "pr_url": pr_url, "pr_number": pr_number, "merged": merged,
            "url": site.get("site_url"), "merge_message": None if merged else _err(merge), "changes": changes}


async def execute_update_approval(args: Dict[str, Any], channel_id: str, approved_by: Optional[str] = None) -> Tuple[str, bool]:
    try:
        res = await apply_update(args, channel_id, approved_by)
    except (SiteBuilderError, gh.GitHubAppError) as e:
        return f"[Update Error]: {e}", True
    n = sum(len(v) for v in res["changes"].values())
    if res["merged"]:
        live = f" Live at {res['url']} (allow about a minute to refresh)." if res.get("url") else ""
        return f"Website updated: pull request #{res['pr_number']} ({n} files) was merged: {res['pr_url']}.{live}", False
    return (
        f"Pull request #{res['pr_number']} with the changes is open at {res['pr_url']}, but GitHub didn't allow an "
        f"automatic merge ({res['merge_message']}). Someone with access needs to merge it on GitHub.", False,
    )
