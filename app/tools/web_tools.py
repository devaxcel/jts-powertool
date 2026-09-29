import ipaddress
import json
import logging
import os
import re
import socket
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import html as py_html
import httpx
from dotenv import load_dotenv

try:
    from bs4 import BeautifulSoup
    HAS_BS4 = True
except ImportError:
    BeautifulSoup = None
    HAS_BS4 = False

load_dotenv()
logger = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# SSRF blocked network definitions
BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local & AWS/cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
]


def sanitize_url(raw_url: str) -> str:
    """
    Cleans raw URLs from Slack formatting (<url|title> or <url>), markdown [text](url),
    trailing punctuation, and surrounding brackets/quotes.
    """
    if not raw_url or not isinstance(raw_url, str):
        return ""

    u = raw_url.strip()

    # Handle Slack link formatting: <https://example.com|Title> or <https://example.com>
    slack_match = re.match(r"^<([^|>]+)(?:\|[^>]+)?>$", u)
    if slack_match:
        u = slack_match.group(1).strip()

    # Handle Markdown link formatting: [Title](https://example.com)
    md_match = re.search(r"\((https?://[^)]+)\)", u)
    if md_match:
        u = md_match.group(1).strip()

    # Strip any enclosing quotes, brackets, parentheses, angles
    u = u.strip("'\"<>[]() \t\r\n")

    # Strip trailing punctuation often appended at sentence ends
    u = re.sub(r"[.,;!)]+$", "", u)
    return u.strip()


def is_safe_url(url: str) -> Tuple[bool, str]:
    """
    Validates that a URL uses http/https and does NOT resolve to
    localhost, link-local, private, or cloud metadata IP addresses.
    """
    clean = sanitize_url(url)
    if not clean:
        return False, "URL cannot be empty."

    try:
        parsed = urlparse(clean)
    except Exception as e:
        return False, f"Invalid URL structure: {e}"

    if parsed.scheme.lower() not in ("http", "https"):
        return False, f"Unsupported URL protocol '{parsed.scheme}'. Only HTTP and HTTPS are permitted."

    hostname = parsed.hostname
    if not hostname:
        return False, "URL does not contain a valid hostname."

    # Check for localhost / loopback aliases directly
    lower_host = hostname.lower()
    if lower_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0", "metadata.google.internal", "instance-data"):
        return False, "Access to localhost or internal metadata service is blocked."

    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)

            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                return False, f"Access to private/internal IP address '{ip_str}' is blocked."

            for net in BLOCKED_IP_NETWORKS:
                if ip in net:
                    return False, f"Access to reserved network range '{net}' is blocked."

        return True, ""
    except socket.gaierror:
        return False, f"Could not resolve domain name '{hostname}'."
    except Exception as e:
        return False, f"Security validation error: {str(e)}"


def clean_html_to_text(html_content: str) -> Tuple[str, str]:
    """
    Converts HTML content into clean readable text/markdown.
    Prioritizes the main article/content container and filters out sidebars/navigation noise.
    Uses BeautifulSoup if available, or a resilient built-in regex parser as fallback.
    Returns (page_title, text_content).
    """
    if not html_content:
        return "", ""

    if HAS_BS4 and BeautifulSoup is not None:
        soup = BeautifulSoup(html_content, "html.parser")

        # Extract title
        title = ""
        title_tag = soup.find("title")
        if title_tag and title_tag.string:
            title = title_tag.string.strip()

        # Remove non-content structural tags
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg", "iframe", "form", "aside"]):
            tag.decompose()

        # Remove common sidebars, table of contents, and ads that pollute article text
        noise_selectors = [
            ".sidebar", ".toc", ".menu", ".nav", ".navigation", ".course-toc",
            ".table-of-contents", "#sidebar", "#nav", ".ad", ".ads", ".advertisement",
            ".social-share", ".related-posts", ".comments", ".tutorial-toc", ".left-menu",
            ".sidebar-wrapper", ".header-wrapper", ".footer-wrapper"
        ]
        for sel in noise_selectors:
            for el in soup.select(sel):
                el.decompose()

        # Find the main content container if available
        main = (
            soup.find(["main", "article"])
            or soup.find(attrs={"role": "main"})
            or soup.find(class_=re.compile(r"(content|article|tutorial-content|post-body|main-content)", re.I))
            or soup.find(id=re.compile(r"(content|article|main|tutorial-content)", re.I))
            or soup.body
            or soup
        )

        # Convert meaningful in-content links
        for a in main.find_all("a"):
            text = a.get_text(strip=True)
            href = a.get("href", "")
            if text and href and not href.startswith("#") and not href.startswith("javascript:"):
                a.replace_with(f" [{text}]({href}) ")

        # Convert headings to markdown
        for i in range(1, 7):
            for h in main.find_all(f"h{i}"):
                h.replace_with(f"\n\n{'#' * i} {h.get_text(strip=True)}\n\n")

        # Format list items
        for li in main.find_all("li"):
            li.replace_with(f"\n- {li.get_text(strip=True)}")

        # Format paragraphs
        for p in main.find_all("p"):
            p.replace_with(f"\n\n{p.get_text(strip=True)}\n\n")

        # Extract clean text
        text = main.get_text()
    else:
        # Resilient built-in standard library fallback (zero external dependencies required)
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html_content, re.IGNORECASE | re.DOTALL)
        title = py_html.unescape(title_match.group(1)).strip() if title_match else ""

        # Remove scripts, styles, headers, footers, navs
        cleaned = re.sub(r"<(script|style|nav|footer|header|noscript|svg|iframe|form|aside)[^>]*>.*?</\1>", "", html_content, flags=re.DOTALL | re.IGNORECASE)
        
        # Convert links to markdown
        cleaned = re.sub(r"<a[^>]+href=[\"'](https?://[^\"']+)[\"'][^>]*>(.*?)</a>", r" [\2](\1) ", cleaned, flags=re.IGNORECASE | re.DOTALL)
        
        # Convert headings
        for i in range(1, 7):
            cleaned = re.sub(rf"<h{i}[^>]*>(.*?)</h{i}>", rf"\n\n{'#' * i} \1\n\n", cleaned, flags=re.IGNORECASE | re.DOTALL)
            
        # Convert list items
        cleaned = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", cleaned, flags=re.IGNORECASE | re.DOTALL)
        
        # Strip remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", cleaned)
        text = py_html.unescape(text)

    # Clean up whitespace
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return title, text.strip()


async def fetch_url_content(url: str, max_chars: int = 15000) -> Dict[str, Any]:
    """
    Safely fetches and extracts the text content from a public URL.
    Returns a dictionary with status, title, url, and content.
    """
    clean_target_url = sanitize_url(url)
    is_safe, error_msg = is_safe_url(clean_target_url)
    if not is_safe:
        return {
            "success": False,
            "url": clean_target_url or url,
            "error": f"URL blocked for security: {error_msg}",
            "content": "",
        }

    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,text/plain;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(clean_target_url, headers=headers)
            response.raise_for_status()

            content_type = response.headers.get("content-type", "").lower()

            if "application/json" in content_type:
                try:
                    data = response.json()
                    text = json.dumps(data, indent=2)
                    title = "JSON API Response"
                except Exception:
                    text = response.text
                    title = "JSON Response"
            elif "text/plain" in content_type or "text/markdown" in content_type:
                text = response.text
                title = "Plain Text / Markdown Document"
            else:
                title, text = clean_html_to_text(response.text)

            is_truncated = False
            if len(text) > max_chars:
                text = text[:max_chars] + f"\n\n[... Content truncated at {max_chars} characters. ...]"
                is_truncated = True

            logger.info(f"[WEB_TOOLS] Successfully fetched URL: '{url}' (title='{title}', {len(text)} chars)")
            return {
                "success": True,
                "url": str(response.url),
                "status_code": response.status_code,
                "title": title or "Webpage",
                "content": text,
                "is_truncated": is_truncated,
            }

    except httpx.HTTPStatusError as e:
        logger.warning(f"[WEB_TOOLS] HTTP {e.response.status_code} error fetching '{url}': {e}")
        return {
            "success": False,
            "url": url,
            "error": f"HTTP {e.response.status_code} {e.response.reason_phrase} when accessing {url}",
            "content": "",
        }
    except httpx.TimeoutException:
        logger.warning(f"[WEB_TOOLS] Timeout connecting to '{url}'")
        return {
            "success": False,
            "url": url,
            "error": f"Connection timed out after 15 seconds while trying to load {url}",
            "content": "",
        }
    except Exception as e:
        logger.error(f"[WEB_TOOLS] Error fetching URL '{url}': {e}", exc_info=True)
        return {
            "success": False,
            "url": url,
            "error": f"Failed to fetch webpage content: {str(e)}",
            "content": "",
        }


async def _search_tavily(query: str, api_key: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Executes a search via Tavily API if key is configured."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "search_depth": "basic",
                    "max_results": max_results,
                },
            )
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data.get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("content", ""),
                    })
                return results
    except Exception as e:
        logger.warning(f"[WEB_TOOLS] Tavily search failed: {e}")
    return None


async def _search_brave(query: str, api_key: str, max_results: int = 5) -> Optional[List[Dict[str, str]]]:
    """Executes a search via Brave Search API if key is configured."""
    try:
        headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": max_results},
                headers=headers,
            )
            if res.status_code == 200:
                data = res.json()
                results = []
                for item in data.get("web", {}).get("results", []):
                    results.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "snippet": item.get("description", ""),
                    })
                return results
    except Exception as e:
        logger.warning(f"[WEB_TOOLS] Brave search failed: {e}")
    return None


async def _search_duckduckgo(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """
    Zero-configuration search via DuckDuckGo lite.
    Requires no API keys and works immediately out of the box.
    Uses BeautifulSoup if available, or regex fallback if missing.
    """
    headers = {
        "User-Agent": DEFAULT_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        res = await client.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": query},
            headers=headers,
        )
        res.raise_for_status()

        results = []
        if HAS_BS4 and BeautifulSoup is not None:
            soup = BeautifulSoup(res.text, "html.parser")
            links = soup.select("a.result-link")
            snippets = soup.select("td.result-snippet")

            for a, s in zip(links, snippets):
                url = a.get("href", "").strip()
                title = a.get_text(strip=True)
                snippet = s.get_text(strip=True)

                if url and title:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                    })
                if len(results) >= max_results:
                    break
        else:
            # Regex extraction fallback
            links = re.findall(r"<a[^>]+class=[\"']result-link[\"'][^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", res.text, re.DOTALL | re.IGNORECASE)
            snippets = re.findall(r"<td[^>]+class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", res.text, re.DOTALL | re.IGNORECASE)
            for (url, raw_title), raw_snip in zip(links, snippets):
                title = py_html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
                snippet = py_html.unescape(re.sub(r"<[^>]+>", "", raw_snip)).strip()
                if url and title:
                    results.append({
                        "title": title,
                        "url": url.strip(),
                        "snippet": snippet,
                    })
                if len(results) >= max_results:
                    break

        return results


def _relax_query(query: str) -> str:
    """Removes conversational fluff, search instructions, and date phrases to get core search terms."""
    q = query
    fluff_patterns = [
        r"^(what\s+are\s+the\s+latest\s+updates\s+about|what\s+is|who\s+is|tell\s+me\s+about|can\s+you\s+tell\s+me\s+about|search\s+for|search\s+the\s+web\s+for)\s+",
        r"\s*(search\s+the\s+web\s+and\s+give\s+me\s+a\s+short\s+summary.*)$",
        r"\s*(give\s+me\s+a\s+short\s+summary.*)$",
        r"\s*(with\s+the\s+sources.*)$",
        r"\s*(with\s+sources.*)$",
    ]
    for p in fluff_patterns:
        q = re.sub(p, " ", q, flags=re.IGNORECASE)
    return " ".join(q.split())


async def search_web(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Searches the live web for a given query string.
    Returns structured results with title, url, and snippet.
    """
    clean_query = query.strip()
    if not clean_query:
        return {
            "success": False,
            "query": query,
            "error": "Search query cannot be empty.",
            "results": [],
            "formatted_summary": "No search query provided.",
        }

    max_results = min(max(1, max_results), 10)
    logger.info(f"[WEB_TOOLS] Executing web search: query='{clean_query}', max_results={max_results}")

    results: Optional[List[Dict[str, str]]] = None
    engine_used = "DuckDuckGo"

    # 1. Try Tavily API if configured
    tavily_key = os.getenv("TAVILY_API_KEY", "").strip()
    if tavily_key:
        results = await _search_tavily(clean_query, tavily_key, max_results)
        if results is not None:
            engine_used = "Tavily"

    # 2. Try Brave Search API if configured
    if results is None:
        brave_key = os.getenv("BRAVE_API_KEY", "").strip()
        if brave_key:
            results = await _search_brave(clean_query, brave_key, max_results)
            if results is not None:
                engine_used = "Brave"

    # 3. Fallback to zero-config DuckDuckGo
    if results is None:
        try:
            results = await _search_duckduckgo(clean_query, max_results)
            engine_used = "DuckDuckGo"

            # If 0 results returned and query was conversational or very specific, try relaxed query
            if not results:
                relaxed = _relax_query(clean_query)
                if relaxed and relaxed.lower() != clean_query.lower():
                    logger.info(f"[WEB_TOOLS] Zero results for '{clean_query}'. Retrying with relaxed query '{relaxed}'")
                    results = await _search_duckduckgo(relaxed, max_results)
        except Exception as e:
            logger.error(f"[WEB_TOOLS] DuckDuckGo search error: {e}", exc_info=True)
            return {
                "success": False,
                "query": clean_query,
                "error": f"Failed to perform web search: {str(e)}",
                "results": [],
                "formatted_summary": f"Web search failed: {str(e)}",
            }

    # Format into a clean summary block for Claude
    if not results:
        formatted = f"No web search results found for query: '{clean_query}'."
    else:
        lines = [f"Web search results for '{clean_query}' ({engine_used}):\n"]
        for idx, r in enumerate(results, 1):
            lines.append(f"{idx}. **[{r['title']}]({r['url']})**")
            if r.get("snippet"):
                lines.append(f"   {r['snippet']}")
            lines.append(f"   URL: {r['url']}\n")
        formatted = "\n".join(lines).strip()

    return {
        "success": True,
        "query": clean_query,
        "engine": engine_used,
        "result_count": len(results),
        "results": results,
        "formatted_summary": formatted,
    }
