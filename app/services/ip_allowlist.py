"""IP allowlisting per client.

A client can be limited to approved networks. The rule applies to that client's dashboard users (Client Admin and Client Standard).
JTS Admins are never limited. It only covers the dashboard: Slack messages come from Slack's servers, not the person's computer.
Entries are single addresses (203.0.113.7) or ranges (203.0.113.0/24, IPv4 or IPv6). An empty list means no limit.
"""
from __future__ import annotations

import ipaddress
import logging
import time
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, Request

from app.db.session import get_db_connection

logger = logging.getLogger(__name__)

MAX_ENTRIES = 50
_CACHE: Dict[int, Tuple[List[str], float]] = {}
_CACHE_TTL = 30.0


def client_ip(request: Request) -> str:
    """The caller's real address. Behind our own proxy (nginx / Next.js on this machine) the address is in X-Forwarded-For:
    read from the right, skipping the loopback hops our own proxies add, so a caller cannot invent their address."""
    peer = request.client.host if request.client else ""
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not peer_ip.is_loopback:
        return str(peer_ip)  # reached directly: the peer is the caller
    hops = [h.strip() for h in (request.headers.get("x-forwarded-for") or "").split(",") if h.strip()]
    for h in reversed(hops):
        try:
            ip = ipaddress.ip_address(h)
        except ValueError:
            continue
        if not ip.is_loopback:
            return str(ip)
    real = (request.headers.get("x-real-ip") or "").strip()
    try:
        return str(ipaddress.ip_address(real)) if real else str(peer_ip)
    except ValueError:
        return str(peer_ip)


def parse_entries(raw: List[str]) -> List[str]:
    """Validates and normalises entries. Raises ValueError with a plain message for the first bad one."""
    out: List[str] = []
    for item in raw or []:
        text = str(item or "").strip()
        if not text:
            continue
        try:
            net = ipaddress.ip_network(text, strict=False)
        except ValueError:
            raise ValueError(f"'{text}' isn't a valid IP address or range (for example 203.0.113.7 or 203.0.113.0/24).")
        norm = str(net.network_address) if net.num_addresses == 1 else str(net)
        if norm not in out:
            out.append(norm)
    if len(out) > MAX_ENTRIES:
        raise ValueError(f"Please keep the list to {MAX_ENTRIES} entries or fewer.")
    return out


def is_allowed(ip: str, entries: List[str]) -> bool:
    if not entries:
        return True
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for e in entries:
        try:
            if addr in ipaddress.ip_network(e, strict=False):
                return True
        except ValueError:
            continue
    return False


def _ensure(cur) -> None:
    cur.execute("ALTER TABLE channel_folders ADD COLUMN IF NOT EXISTS ip_allowlist TEXT;")


def get_entries(folder_id: int, use_cache: bool = True) -> List[str]:
    if not folder_id or int(folder_id) < 0:
        return []
    fid = int(folder_id)
    hit = _CACHE.get(fid)
    if use_cache and hit and time.time() < hit[1]:
        return list(hit[0])
    entries: List[str] = []
    try:
        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                _ensure(cur)
                conn.commit()
                cur.execute("SELECT ip_allowlist FROM channel_folders WHERE id = %s;", (fid,))
                row = cur.fetchone()
                raw = (row["ip_allowlist"] if isinstance(row, dict) else (row[0] if row else "")) or ""
                entries = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"[IP_ALLOWLIST] Could not read the list for client {fid}: {e}")
        return []  # a broken lookup must not lock a whole client out
    _CACHE[fid] = (entries, time.time() + _CACHE_TTL)
    return list(entries)


def set_entries(folder_id: int, raw: List[str]) -> List[str]:
    entries = parse_entries(raw)
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            _ensure(cur)
            cur.execute("UPDATE channel_folders SET ip_allowlist = %s WHERE id = %s RETURNING id;", ("\n".join(entries) or None, int(folder_id)))
            if not cur.fetchone():
                raise LookupError("That client doesn't exist.")
        conn.commit()
    finally:
        conn.close()
    _CACHE.pop(int(folder_id), None)
    return entries


def enforce(request: Request, role: Optional[str], folder_id: Optional[int]) -> None:
    """Raises 403 when this client's list exists and the caller's address is not on it. JTS Admins are never limited."""
    if role not in ("client_admin", "client_standard") or not folder_id or int(folder_id) < 0:
        return
    entries = get_entries(int(folder_id))
    if not entries:
        return
    ip = client_ip(request)
    if not is_allowed(ip, entries):
        logger.warning(f"[IP_ALLOWLIST] Blocked {ip} for client {folder_id}")
        raise HTTPException(
            status_code=403,
            detail=f"Your organization only allows access from approved networks, and your address ({ip}) isn't one of them. "
                   "Ask your JTS administrator to add it.",
        )
