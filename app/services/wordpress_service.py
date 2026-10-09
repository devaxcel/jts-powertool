"""WordPress: read a client's own WordPress site and change its pages and posts, only after a person approves.

How it connects
  The site owner makes an *Application Password* (WordPress admin -> Users -> Profile) and enters the site address, the username and that
  password on the client's page. The password is kept encrypted in AWS (hidden provider "wordpress_app") and is never shown again. It can be
  revoked in WordPress at any time. Everything goes through WordPress's own REST API on that one site, over HTTPS only.

Editors
  Gutenberg (blocks) and the Classic editor are read and written directly through the REST API.
  Elementor keeps its text in hidden page data, so editing it needs the small "JTS PowerTool Connector" plugin on the site
  (app/assets/jts-powertool-connector.php); only a fixed list of text fields can be changed, never layout or styles.
  Other builders (Divi, WPBakery, ...) are read-only for now: the bot says so instead of risking a broken page.

What it can never do: delete for good (it only moves to Trash), create or change users, install plugins, edit theme or plugin files.
"""
from __future__ import annotations

import base64
import html as _html
import ipaddress
import json
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

PROVIDER = "wordpress_app"
WP_READ_TOOLS = {"wp_list_content", "wp_get_content"}
WP_WRITE_TOOLS = {"wp_update_content", "wp_create_content", "wp_set_status", "wp_update_elementor_text"}
WP_TOOLS = WP_READ_TOOLS | WP_WRITE_TOOLS | {"connect_wordpress"}

MAX_CONTENT = 200_000
MAX_VIEW = 60_000
STATUSES = ("draft", "pending", "private", "publish", "future", "trash")
PUBLISHING = ("publish", "future", "private")
SUPPORTED_EDITORS = ("gutenberg", "classic")
EDITOR_LABELS = {
    "gutenberg": "Gutenberg (blocks)", "classic": "Classic editor", "elementor": "Elementor", "divi": "Divi",
    "wpbakery": "WPBakery", "other_builder": "another page builder", "empty": "an empty page",
}
TIMEOUT = 25.0


class WordPressError(Exception):
    """A problem with a plain message for the person."""


# --------------------------------------------------------------------------- the site address

def normalize_site_url(raw: str) -> str:
    """'Example.com/wp-admin/' -> 'https://example.com'. HTTPS only, no logins in the address, no odd ports."""
    text = (raw or "").strip()
    if not text:
        raise WordPressError("Enter the address of the WordPress site, for example https://www.example.com")
    if "://" not in text:
        text = "https://" + text
    u = urlparse(text)
    if u.scheme != "https":
        raise WordPressError("The site address must start with https:// (a secure site is required).")
    if u.username or u.password:
        raise WordPressError("Don't put a username or password in the address.")
    host = (u.hostname or "").lower()
    if not host or not re.fullmatch(r"[a-z0-9.-]+", host) or ("." not in host and not _is_ip(host)):
        raise WordPressError("That doesn't look like a website address.")
    if u.port not in (None, 443):
        raise WordPressError("Use the normal https address of the site (no special port).")
    path = re.sub(r"/(wp-admin|wp-login\.php|wp-json|wp-content).*$", "", u.path or "", flags=re.I).rstrip("/")
    if not re.fullmatch(r"(/[A-Za-z0-9._~-]+)*", path):
        raise WordPressError("The site address contains characters that can't be used.")
    return f"https://{host}{path}"


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _resolve_ips(host: str) -> List[str]:
    return sorted({info[4][0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})


_HOST_OK: Dict[str, float] = {}


def assert_public_host(host: str) -> None:
    """Refuses addresses that point inside our own network (so the bot can't be pointed at internal services)."""
    now = time.time()
    if _HOST_OK.get(host, 0) > now:
        return
    try:
        ips = [host] if _is_ip(host) else _resolve_ips(host)
    except OSError:
        raise WordPressError("That site address can't be found. Check the spelling.")
    if not ips:
        raise WordPressError("That site address can't be found. Check the spelling.")
    for ip in ips:
        try:
            if not ipaddress.ip_address(ip).is_global:
                raise WordPressError("That address points to a private network, not a public website.")
        except ValueError:
            raise WordPressError("That site address can't be used.")
    _HOST_OK[host] = now + 300


# --------------------------------------------------------------------------- storage

def _db(fn):
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS wordpress_connections (
                    id SERIAL PRIMARY KEY,
                    folder_id INTEGER UNIQUE NOT NULL,
                    site_url VARCHAR(300) NOT NULL,
                    site_name VARCHAR(255),
                    wp_user VARCHAR(255),
                    wp_user_name VARCHAR(255),
                    wp_roles VARCHAR(255),
                    rest_style VARCHAR(10) DEFAULT 'pretty',
                    editors TEXT,
                    editors_checked_at TIMESTAMP WITH TIME ZONE,
                    status VARCHAR(20) DEFAULT 'active',
                    connected_by VARCHAR(255),
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
            result = fn(cur)
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_connection(folder_id: Optional[int]) -> Optional[Dict[str, Any]]:
    if not folder_id:
        return None

    def run(cur):
        cur.execute("SELECT * FROM wordpress_connections WHERE folder_id = %s;", (folder_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    row = _db(run)
    if not row:
        return None
    for k in ("created_at", "updated_at", "editors_checked_at"):
        if row.get(k) is not None and hasattr(row[k], "isoformat"):
            row[k] = row[k].isoformat()
    try:
        row["editors"] = json.loads(row.get("editors") or "{}")
    except ValueError:
        row["editors"] = {}
    return row


def _save_connection(folder_id: int, site_url: str, site_name: str, me: Dict[str, Any], rest_style: str, by: str) -> None:
    roles = ", ".join(me.get("roles") or [])

    def run(cur):
        cur.execute(
            """
            INSERT INTO wordpress_connections (folder_id, site_url, site_name, wp_user, wp_user_name, wp_roles, rest_style, status, connected_by, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, CURRENT_TIMESTAMP)
            ON CONFLICT (folder_id) DO UPDATE SET site_url = EXCLUDED.site_url, site_name = EXCLUDED.site_name, wp_user = EXCLUDED.wp_user,
                wp_user_name = EXCLUDED.wp_user_name, wp_roles = EXCLUDED.wp_roles, rest_style = EXCLUDED.rest_style, status = 'active',
                connected_by = EXCLUDED.connected_by, updated_at = CURRENT_TIMESTAMP;
            """,
            (folder_id, site_url, site_name, me.get("slug") or me.get("username") or "", me.get("name") or "", roles, rest_style, by),
        )

    _db(run)


def _save_editors(folder_id: int, editors: Dict[str, int]) -> None:
    _db(lambda cur: cur.execute(
        "UPDATE wordpress_connections SET editors = %s, editors_checked_at = CURRENT_TIMESTAMP WHERE folder_id = %s;",
        (json.dumps(editors), folder_id)))


def _set_status(folder_id: int, status: str) -> None:
    _db(lambda cur: cur.execute("UPDATE wordpress_connections SET status = %s, updated_at = CURRENT_TIMESTAMP WHERE folder_id = %s;", (status, folder_id)))


def disconnect(folder_id: int) -> bool:
    from app.services.channel_secrets_service import delete_folder_api_key

    def run(cur):
        cur.execute("DELETE FROM wordpress_connections WHERE folder_id = %s;", (folder_id,))
        return cur.rowcount

    removed = bool(_db(run))
    try:
        delete_folder_api_key(folder_id, PROVIDER)
    except Exception as e:
        logger.warning(f"[WORDPRESS] Could not remove the stored password for client {folder_id}: {type(e).__name__}")
    _CONNECTOR_CACHE.clear()
    return removed


def _password(folder_id: int) -> str:
    from app.services.channel_secrets_service import get_folder_api_key_value

    pw = get_folder_api_key_value(folder_id, PROVIDER)
    if not pw:
        raise WordPressError("The saved WordPress password is missing. Connect the site again.")
    return pw


async def resolve_connection(channel_id: str) -> Dict[str, Any]:
    from app.services.channel_secrets_service import get_folder_id_for_channel

    folder_id = get_folder_id_for_channel(channel_id)
    if not folder_id:
        raise WordPressError("This Slack channel isn't linked to a client yet.")
    conn = get_connection(folder_id)
    if not conn:
        raise WordPressError("This client hasn't connected a WordPress site yet. Ask a Client Admin to connect it under Keys & Connections.")
    if conn.get("status") != "active":
        raise WordPressError("This client's WordPress connection needs to be set up again (Keys & Connections).")
    return conn


# --------------------------------------------------------------------------- talking to the site

def _auth_header(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")


def _url(base: str, rest_style: str, route: str, params: Optional[Dict[str, Any]] = None) -> Tuple[str, Dict[str, Any]]:
    params = dict(params or {})
    route = "/" + route.lstrip("/")
    if rest_style == "query":
        params["rest_route"] = route
        return f"{base}/", params
    return f"{base}/wp-json{route}", params


async def _send(base: str, rest_style: str, method: str, route: str, auth: Optional[str], params=None, json_body=None) -> httpx.Response:
    host = urlparse(base).hostname or ""
    assert_public_host(host)
    url, q = _url(base, rest_style, route, params)
    headers = {"Accept": "application/json", "User-Agent": "JTS-PowerTool/1.0"}
    if auth:
        headers["Authorization"] = auth
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False) as client:
        resp = await client.request(method, url, params=q, headers=headers, json=json_body)
        if resp.status_code in (301, 302, 307, 308):
            loc = resp.headers.get("location", "")
            lu = urlparse(loc)
            if lu.scheme == "https" and (lu.hostname or "").lower() == host:  # the same site (for example www or a trailing slash)
                resp = await client.request(method, loc, headers=headers, json=json_body)
            else:
                raise WordPressError("The site sends visitors to another address. Use the site's exact https address.")
    return resp


async def _api(conn: Dict[str, Any], method: str, route: str, params=None, json_body=None) -> httpx.Response:
    auth = _auth_header(conn["wp_user"], _password(conn["folder_id"]))
    return await _send(conn["site_url"], conn.get("rest_style") or "pretty", method, route, auth, params, json_body)


def _explain(resp: httpx.Response) -> str:
    code, message = "", ""
    try:
        data = resp.json()
        code, message = str(data.get("code") or ""), str(data.get("message") or "")
    except ValueError:
        pass
    s = resp.status_code
    if s == 401 or code in ("incorrect_password", "invalid_username", "rest_not_logged_in"):
        return "WordPress didn't accept that username and application password."
    if s == 403:
        return "WordPress refused this (the user may lack permission, or a security plugin or the host is blocking the request)."
    if s == 404 and code in ("rest_no_route", "rest_post_invalid_id"):
        return "WordPress couldn't find that page or post."
    if s == 404:
        return "WordPress's REST API wasn't found on that address. Check the address, or ask the host to allow the REST API."
    if s == 409:
        return message or "The page changed in the meantime."
    if s >= 500:
        return "The WordPress site had an error. Try again in a minute."
    return (message or f"HTTP {s}")[:300]


# --------------------------------------------------------------------------- connect

async def connect_site(folder_id: int, site_url: str, username: str, app_password: str, by: str = "admin") -> Dict[str, Any]:
    from app.services.channel_secrets_service import store_folder_api_key

    base = normalize_site_url(site_url)
    username = (username or "").strip()
    password = re.sub(r"\s+", "", app_password or "")
    if not username or not password:
        raise WordPressError("Enter the WordPress username and the application password.")
    assert_public_host(urlparse(base).hostname or "")

    # 1. is it WordPress with the REST API on? (public call)
    rest_style, root = "pretty", None
    for style in ("pretty", "query"):
        try:
            r = await _send(base, style, "GET", "/", None)
            data = r.json() if r.status_code == 200 else None
        except (ValueError, httpx.HTTPError):
            data = None
        if isinstance(data, dict) and "namespaces" in data:
            rest_style, root = style, data
            break
    if not root or "wp/v2" not in (root.get("namespaces") or []):
        raise WordPressError("That doesn't look like a WordPress site with its REST API switched on. Check the address, or ask the host to allow the REST API.")

    # 2. do the credentials work, and may this user edit pages?
    auth = _auth_header(username, password)
    try:
        r = await _send(base, rest_style, "GET", "/wp/v2/users/me", auth, params={"context": "edit"})
    except httpx.HTTPError:
        raise WordPressError("The site didn't answer. Try again in a minute.")
    if r.status_code != 200:
        raise WordPressError(_explain(r))
    me = r.json()
    caps = me.get("capabilities") or {}
    if not (caps.get("edit_pages") or caps.get("edit_posts")):
        raise WordPressError("That WordPress user can't edit pages or posts. Use an Editor or Administrator account.")

    # 3. keep the password in AWS, the rest in the database
    try:
        store_folder_api_key(folder_id=folder_id, api_key=password, provider=PROVIDER, updated_by=by)
    except LookupError as e:
        raise WordPressError(str(e))
    name = (root.get("name") or urlparse(base).hostname or "").strip()
    _save_connection(folder_id, base, name, {**me, "slug": username}, rest_style, by)
    conn = get_connection(folder_id)
    editors: Dict[str, int] = {}
    try:
        editors = await sample_editors(conn)
        _save_editors(folder_id, editors)
    except Exception as e:
        logger.warning(f"[WORDPRESS] Could not sample the editors after connecting: {type(e).__name__}")
    logger.info(f"[WORDPRESS] Client {folder_id} connected {base} as '{username}' (by {by})")
    return {"site_url": base, "site_name": name, "user": me.get("name"), "roles": me.get("roles") or [], "editors": editors}


# --------------------------------------------------------------------------- editors

def detect_editor(item: Dict[str, Any]) -> str:
    """Which editor made this page: gutenberg, classic, elementor, divi, wpbakery, other_builder or empty."""
    c = item.get("content") or {}
    raw = c.get("raw") or ""
    rendered = c.get("rendered") or ""
    low = rendered.lower()
    if "<!-- wp:" in raw:
        return "gutenberg"
    if "[et_pb_" in raw:
        return "divi"
    if "[vc_" in raw:
        return "wpbakery"
    if "data-elementor-type" in low or "elementor-element" in low or "elementor-section" in low or 'class="elementor' in low:
        return "elementor"
    if not raw.strip():
        return "other_builder" if rendered.strip() else "empty"
    return "classic"


async def sample_editors(conn: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for base, n in (("pages", 20), ("posts", 10)):
        r = await _api(conn, "GET", f"/wp/v2/{base}", params={"context": "edit", "per_page": n, "status": "any", "orderby": "modified", "_fields": "id,content"})
        if r.status_code != 200:
            continue
        for item in r.json():
            e = detect_editor(item)
            counts[e] = counts.get(e, 0) + 1
    counts["sampled"] = sum(v for k, v in counts.items() if k != "sampled")
    return counts


_CONNECTOR_CACHE: Dict[int, Tuple[Dict[str, Any], float]] = {}


async def connector_info(conn: Dict[str, Any], fresh: bool = False) -> Dict[str, Any]:
    """Is the JTS PowerTool Connector plugin installed on the site (needed for Elementor text)?"""
    fid = conn["folder_id"]
    hit = _CONNECTOR_CACHE.get(fid)
    if not fresh and hit and time.time() < hit[1]:
        return hit[0]
    info: Dict[str, Any] = {"installed": False, "version": None, "elementor": False, "elementor_version": None}
    try:
        r = await _api(conn, "GET", "/jts/v1/ping")
        if r.status_code == 200:
            d = r.json()
            info = {"installed": True, "version": d.get("version"), "elementor": bool(d.get("elementor")), "elementor_version": d.get("elementor_version")}
    except (WordPressError, httpx.HTTPError, ValueError):
        pass
    _CONNECTOR_CACHE[fid] = (info, time.time() + 60)
    return info


async def refresh_status(folder_id: int) -> Dict[str, Any]:
    """Checks the connection still works and re-counts the editors. Marks it 'expired' when WordPress rejects the password."""
    conn = get_connection(folder_id)
    if not conn:
        raise WordPressError("WordPress isn't connected.")
    r = await _api(conn, "GET", "/wp/v2/users/me", params={"context": "edit"})
    if r.status_code in (401, 403):
        _set_status(folder_id, "expired")
        raise WordPressError("WordPress no longer accepts the saved password (it may have been revoked). Connect the site again.")
    if r.status_code != 200:
        raise WordPressError(_explain(r))
    if conn.get("status") != "active":
        _set_status(folder_id, "active")
    editors = await sample_editors(conn)
    _save_editors(folder_id, editors)
    return {"editors": editors, "connector": await connector_info(conn, fresh=True)}


# --------------------------------------------------------------------------- helpers for content

_TYPE_RE = re.compile(r"^[a-z0-9_-]{1,40}$")


def rest_base(content_type: str) -> str:
    t = (content_type or "page").strip().lower()
    if t in ("page", "pages"):
        return "pages"
    if t in ("post", "posts"):
        return "posts"
    if not _TYPE_RE.match(t):
        raise WordPressError("Say 'page' or 'post' (or the custom type's name).")
    return t


def strip_html(text: str, limit: int = 0) -> str:
    t = re.sub(r"<!--.*?-->", "", text or "", flags=re.S)
    t = re.sub(r"</(p|h[1-6]|li|div|blockquote|tr)>|<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    t = _html.unescape(t)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    return t[:limit] if limit else t


def html_to_blocks(content: str) -> str:
    """Wraps plain HTML into Gutenberg blocks so the page opens normally in the block editor. Existing blocks are left alone."""
    text = (content or "").strip()
    if not text or "<!-- wp:" in text:
        return text
    out: List[str] = []
    pattern = re.compile(r"<(p|h[1-6]|ul|ol|blockquote)\b[^>]*>.*?</\1>", re.S | re.I)
    pos = 0
    for m in pattern.finditer(text):
        gap = text[pos:m.start()].strip()
        if gap:
            out.append(_wrap_custom(gap))
        tag, block = m.group(1).lower(), m.group(0)
        if tag == "p":
            out.append(f"<!-- wp:paragraph -->\n{block}\n<!-- /wp:paragraph -->")
        elif tag.startswith("h"):
            level = int(tag[1])
            attrs = "" if level == 2 else f' {{"level":{level}}}'
            out.append(f"<!-- wp:heading{attrs} -->\n{block}\n<!-- /wp:heading -->")
        elif tag in ("ul", "ol"):
            attrs = ' {"ordered":true}' if tag == "ol" else ""
            out.append(f"<!-- wp:list{attrs} -->\n{block}\n<!-- /wp:list -->")
        else:
            out.append(f"<!-- wp:quote -->\n{block}\n<!-- /wp:quote -->")
        pos = m.end()
    tail = text[pos:].strip()
    if tail:
        out.append(_wrap_custom(tail) if "<" in tail else f"<!-- wp:paragraph -->\n<p>{_html.escape(tail)}</p>\n<!-- /wp:paragraph -->")
    return "\n\n".join(out)


def _wrap_custom(fragment: str) -> str:
    if "<" not in fragment:
        return f"<!-- wp:paragraph -->\n<p>{_html.escape(fragment)}</p>\n<!-- /wp:paragraph -->"
    return f"<!-- wp:html -->\n{fragment}\n<!-- /wp:html -->"


def _item_view(item: Dict[str, Any]) -> Dict[str, Any]:
    c = item.get("content") or {}
    t = item.get("title") or {}
    ex = item.get("excerpt") or {}
    return {
        "id": item.get("id"), "title": t.get("raw") if isinstance(t, dict) and "raw" in t else _html.unescape(strip_html(str(t.get("rendered") or ""))),
        "status": item.get("status"), "slug": item.get("slug"), "link": item.get("link"), "modified": item.get("modified_gmt") or item.get("modified"),
        "content": c.get("raw") or "", "excerpt": ex.get("raw") if isinstance(ex, dict) and "raw" in ex else strip_html(str(ex.get("rendered") or "")),
        "editor": detect_editor(item), "type": item.get("type"),
    }


async def fetch_item(conn: Dict[str, Any], content_type: str, item_id: int) -> Dict[str, Any]:
    r = await _api(conn, "GET", f"/wp/v2/{rest_base(content_type)}/{int(item_id)}", params={"context": "edit"})
    if r.status_code != 200:
        raise WordPressError(_explain(r))
    return _item_view(r.json())


async def elementor_nodes(conn: Dict[str, Any], item_id: int) -> Dict[str, Any]:
    info = await connector_info(conn)
    if not info["installed"]:
        raise WordPressError(
            "This page is built with Elementor. To read or change its text, the free 'JTS PowerTool Connector' plugin must be installed on the "
            "site (Keys & Connections -> WordPress -> Download connector plugin)."
        )
    r = await _api(conn, "GET", f"/jts/v1/elementor/{int(item_id)}")
    if r.status_code != 200:
        raise WordPressError(_explain(r))
    return r.json()


# --------------------------------------------------------------------------- read tools

async def run_read_tool(channel_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    try:
        conn = await resolve_connection(channel_id)
        if tool == "wp_list_content":
            base = rest_base(args.get("type") or "page")
            per_page = max(1, min(int(args.get("per_page") or 15), 20))
            params: Dict[str, Any] = {"context": "edit", "per_page": per_page, "page": max(1, int(args.get("page") or 1)), "orderby": "modified", "order": "desc",
                                      "status": args.get("status") or "any", "_fields": "id,title,status,slug,link,modified_gmt,content,type"}
            if args.get("search"):
                params["search"] = str(args["search"])[:100]
            r = await _api(conn, "GET", f"/wp/v2/{base}", params=params)
            if r.status_code != 200:
                return (f"[WordPress Error]: {_explain(r)}", True)
            items = [_item_view(i) for i in r.json()]
            if not items:
                return ("Nothing matched.", False)
            lines = [f"#{i['id']} [{i['status']}] \"{i['title']}\" - {EDITOR_LABELS.get(i['editor'], i['editor'])} - modified {str(i['modified'])[:10]} - {i['link']}" for i in items]
            return (f"{len(items)} {base} on {conn['site_name']}:\n" + "\n".join(lines), False)
        if tool == "wp_get_content":
            item = await fetch_item(conn, args.get("type") or "page", int(args.get("id") or 0))
            head = (f"{item['type']} #{item['id']} \"{item['title']}\" [{item['status']}] - {EDITOR_LABELS.get(item['editor'], item['editor'])}\n"
                    f"link: {item['link']}\nslug: {item['slug']}\nmodified: {item['modified']}\n")
            if item["editor"] == "elementor":
                data = await elementor_nodes(conn, item["id"])
                lines = [f"- element {n['element_id']} ({n['widget']}) field {n['path']}: {n['text'][:600]}" for n in data.get("nodes", [])][:150]
                return (head + "This page is built with Elementor. Its editable texts are below; change them with wp_update_elementor_text "
                        "(only text, never layout):\n" + "\n".join(lines), False)
            if item["editor"] not in SUPPORTED_EDITORS:
                return (head + f"This page was made with {EDITOR_LABELS.get(item['editor'], item['editor'])}, which isn't supported for editing yet. "
                        "You can read it, but suggest the new text to the user instead of changing it.\n\nVisible text:\n" + strip_html(item["content"], 4000), False)
            body = item["content"]
            note = "" if len(body) <= MAX_VIEW else f"\n[Content shortened: showing the first {MAX_VIEW:,} of {len(body):,} characters]"
            return (head + (f"excerpt: {item['excerpt']}\n" if item["excerpt"] else "") + "\nCONTENT (raw):\n" + body[:MAX_VIEW] + note, False)
    except WordPressError as e:
        return (f"[WordPress Error]: {e}", True)
    except httpx.TimeoutException:
        return ("[WordPress Error]: The site didn't answer in time. Try again in a minute.", True)
    except Exception as e:
        logger.error(f"[WORDPRESS] {tool} failed: {type(e).__name__}: {e}")
        return ("[WordPress Error]: Something went wrong talking to the site.", True)
    return (f"[WordPress Error]: Unknown tool {tool}.", True)


# --------------------------------------------------------------------------- write tools: propose (validate + snapshot), then execute

def _clean_text(v: Any, limit: int) -> str:
    return str(v or "").strip()[:limit]


def _check_status(status: str, date: Optional[str]) -> Tuple[str, Optional[str]]:
    s = (status or "draft").strip().lower()
    if s not in STATUSES:
        raise WordPressError("Status must be one of: draft, pending, private, publish, future (scheduled), trash.")
    if s == "future":
        if not date or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", str(date)):
            raise WordPressError("To schedule, give the date and time like 2026-11-03T09:00:00.")
        return s, str(date)[:19]
    return s, None


async def prepare_write(channel_id: str, tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Validates a requested change, reads the current page, and returns what is stored for approval (with a 'previous' snapshot)."""
    conn = await resolve_connection(channel_id)
    site = {"site_name": conn.get("site_name"), "site_url": conn.get("site_url")}
    ctype = args.get("type") or "page"
    if tool == "wp_create_content":
        title = _clean_text(args.get("title"), 300)
        if not title:
            raise WordPressError("A new page or post needs a title.")
        status, date = _check_status(args.get("status") or "draft", args.get("date"))
        if status == "trash":
            raise WordPressError("A new page can't be created in the trash.")
        content = _clean_text(args.get("content"), MAX_CONTENT)
        if (conn.get("editors") or {}).get("gutenberg", 0) >= (conn.get("editors") or {}).get("classic", 0):
            content = html_to_blocks(content)
        eff: Dict[str, Any] = {"type": ctype, "title": title, "content": content, "status": status, **site}
        for k, lim in (("slug", 100), ("excerpt", 1000)):
            if args.get(k):
                eff[k] = _clean_text(args[k], lim)
        if date:
            eff["date"] = date
        if args.get("parent"):
            eff["parent"] = int(args["parent"])
        return eff

    item_id = int(args.get("id") or 0)
    if item_id <= 0:
        raise WordPressError("Give the id of the page or post (use wp_list_content to find it).")
    item = await fetch_item(conn, ctype, item_id)
    previous = {k: item[k] for k in ("title", "content", "excerpt", "slug", "status", "modified", "link", "editor")}

    if tool == "wp_update_content":
        if item["editor"] == "elementor":
            raise WordPressError("This page is built with Elementor, so its text must be changed with wp_update_elementor_text, not wp_update_content.")
        if item["editor"] not in SUPPORTED_EDITORS:
            raise WordPressError(f"This page was made with {EDITOR_LABELS.get(item['editor'], item['editor'])}, which can't be edited safely yet. Offer the new text for the user to paste instead.")
        eff = {"type": ctype, "id": item_id, **site, "previous": previous}
        changed = False
        for k, lim in (("title", 300), ("content", MAX_CONTENT), ("excerpt", 1000), ("slug", 100)):
            if args.get(k) is not None:
                val = _clean_text(args[k], lim) if k != "content" else str(args[k])[:lim]
                if k == "content" and item["editor"] == "gutenberg":
                    val = html_to_blocks(val)
                if val != previous[k]:
                    eff[k] = val
                    changed = True
        if not changed:
            raise WordPressError("Nothing would change: the new text is the same as what is on the page now.")
        return eff

    if tool == "wp_set_status":
        status, date = _check_status(args.get("status"), args.get("date"))
        if status == previous["status"] and status != "future":
            raise WordPressError(f"The {item['type']} is already {status}.")
        eff = {"type": ctype, "id": item_id, "status": status, **site, "previous": previous, "title": previous["title"]}
        if date:
            eff["date"] = date
        return eff

    if tool == "wp_update_elementor_text":
        if item["editor"] != "elementor":
            raise WordPressError("This page is not built with Elementor. Use wp_update_content for it.")
        data = await elementor_nodes(conn, item_id)
        nodes = {(n["element_id"], n["path"]): n for n in data.get("nodes", [])}
        changes = args.get("changes")
        if not isinstance(changes, list) or not changes:
            raise WordPressError("Give at least one change: {element_id, path, new_value}, using the ids from wp_get_content.")
        if len(changes) > 100:
            raise WordPressError("Please change at most 100 texts at a time.")
        out = []
        for ch in changes:
            key = (str(ch.get("element_id") or ""), str(ch.get("path") or ""))
            node = nodes.get(key)
            if not node:
                raise WordPressError(f"Element {key[0]} / {key[1]} isn't an editable text on this page. Read the page again with wp_get_content.")
            new = ch.get("new_value")
            if not isinstance(new, str):
                raise WordPressError("Each change needs a new_value (text).")
            if new == node["text"]:
                continue
            out.append({"element_id": key[0], "path": key[1], "widget": node["widget"], "kind": node["kind"], "old": node["text"][:2000], "new_value": new[:20000]})
        if not out:
            raise WordPressError("Nothing would change: the new texts are the same as the page now.")
        previous["modified"] = data.get("modified") or previous["modified"]
        return {"type": ctype, "id": item_id, "title": previous["title"], "changes": out, **site, "previous": previous}

    raise WordPressError(f"Unknown tool {tool}.")


async def execute_write(channel_id: str, tool: str, args: Dict[str, Any]) -> Tuple[str, bool]:
    """Runs an approved change. Refuses if the page was edited by someone else after the proposal."""
    try:
        conn = await resolve_connection(channel_id)
        ctype = args.get("type") or "page"
        base = rest_base(ctype)
        if tool == "wp_create_content":
            body: Dict[str, Any] = {"title": args["title"], "content": args.get("content") or "", "status": args.get("status") or "draft"}
            for k in ("slug", "excerpt", "date", "parent"):
                if args.get(k):
                    body[k] = args[k]
            r = await _api(conn, "POST", f"/wp/v2/{base}", json_body=body)
            if r.status_code not in (200, 201):
                return (f"[WordPress Error]: {_explain(r)}", True)
            d = r.json()
            edit = f"{conn['site_url']}/wp-admin/post.php?post={d.get('id')}&action=edit"
            state = "draft" if d.get("status") == "draft" else d.get("status")
            return (f"Created {state} {args.get('type') or 'page'} \"{args['title']}\" (#{d.get('id')}): {d.get('link')} - edit in WordPress: {edit}", False)

        item_id = int(args["id"])
        prev = args.get("previous") or {}
        current = await fetch_item(conn, ctype, item_id)
        if tool == "wp_update_elementor_text":
            data = await elementor_nodes(conn, item_id)
            cur_mod = data.get("modified")
        else:
            cur_mod = current["modified"]
        if prev.get("modified") and cur_mod and str(prev["modified"]) != str(cur_mod):
            return ("[WordPress Error]: The page was changed on the site after this change was proposed, so nothing was changed. Ask again so the newest version is used.", True)

        if tool == "wp_update_content":
            body = {k: args[k] for k in ("title", "content", "excerpt", "slug") if k in args}
            r = await _api(conn, "POST", f"/wp/v2/{base}/{item_id}", json_body=body)
            if r.status_code != 200:
                return (f"[WordPress Error]: {_explain(r)}", True)
            d = r.json()
            return (f"Updated \"{current['title']}\" (#{item_id}, {d.get('status')}): {d.get('link')}. WordPress keeps the earlier version under Revisions.", False)
        if tool == "wp_set_status":
            status = args["status"]
            if status == "trash":
                r = await _api(conn, "DELETE", f"/wp/v2/{base}/{item_id}")  # no force=true: moves to the trash, can be restored
            else:
                body = {"status": status}
                if args.get("date"):
                    body["date"] = args["date"]
                r = await _api(conn, "POST", f"/wp/v2/{base}/{item_id}", json_body=body)
            if r.status_code != 200:
                return (f"[WordPress Error]: {_explain(r)}", True)
            word = {"publish": "published", "future": "scheduled", "trash": "moved to the Trash (it can be restored there)"}.get(status, f"set to {status}")
            return (f"\"{current['title']}\" (#{item_id}) was {word}: {current['link']}", False)
        if tool == "wp_update_elementor_text":
            body = {"changes": [{"element_id": c["element_id"], "path": c["path"], "new_value": c["new_value"]} for c in args["changes"]], "expected_modified": cur_mod}
            r = await _api(conn, "POST", f"/jts/v1/elementor/{item_id}", json_body=body)
            if r.status_code != 200:
                return (f"[WordPress Error]: {_explain(r)}", True)
            return (f"Changed {r.json().get('applied', len(body['changes']))} text(s) on \"{current['title']}\" (#{item_id}): {current['link']}", False)
    except WordPressError as e:
        return (f"[WordPress Error]: {e}", True)
    except httpx.TimeoutException:
        return ("[WordPress Error]: The site didn't answer in time. Nothing was confirmed; check the page before trying again.", True)
    except Exception as e:
        logger.error(f"[WORDPRESS] {tool} failed: {type(e).__name__}: {e}")
        return ("[WordPress Error]: Something went wrong talking to the site.", True)
    return (f"[WordPress Error]: Unknown tool {tool}.", True)


# --------------------------------------------------------------------------- prompt

def prompt_text(conn: Optional[Dict[str, Any]]) -> str:
    if not conn or conn.get("status") != "active":
        return ""
    return (
        f"\n\nWORDPRESS (this client's site: {conn.get('site_name')}, {conn.get('site_url')}). Use the wp_* tools for anything about this website's pages or posts. "
        "Always read a page with wp_get_content before changing it, and keep every part of the page you were not asked to change exactly as it is. "
        "Pages made with Gutenberg keep their block markup (<!-- wp:paragraph --> ...); Classic pages are plain HTML. For Elementor pages change only text, "
        "with wp_update_elementor_text, using the element ids you read; never promise layout or style changes. Pages made with other builders can only be read. "
        "New pages and posts are created as DRAFTS; publish, schedule or trash only when the user clearly asks to. Nothing is changed until a person approves the "
        "approval card, so call the tool to propose a change (never just describe it), then say in one short line what you proposed. "
        "To change several pages, propose them one at a time. If the user wants something these tools can't do (plugins, theme files, users, settings), say so plainly."
    )
