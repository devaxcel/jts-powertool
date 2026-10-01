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
6. To change an already published site, explain that edits will be proposed as changes for approval.
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
