"""GitHub rules for the assistant (scope section 8).

  - Commits and pull requests reference the Jira ticket number (when the client uses Jira).
  - Non-trivial changes never go straight to the main branch: they go on a branch and into a pull request.
  - The assistant never force-pushes, and it has no way to merge: a person reviews and merges on GitHub.
  - Every change still waits for an approval card.

The checks are plain code (not instructions to the AI), so the assistant cannot talk its way around them.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("github_rules")

JIRA_KEY = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
PROTECTED_BRANCHES = {"main", "master"}
TRIVIAL_MAX_CHARS = 1500
DOC_SUFFIXES = (".md", ".txt", ".rst")
RULED_TOOLS = {"create_or_update_file", "push_files", "create_pull_request"}


def tickets_in(text: str) -> List[str]:
    return JIRA_KEY.findall(text or "")


def has_ticket(text: str) -> bool:
    return bool(JIRA_KEY.search(text or ""))


def message_field(tool: str) -> str:
    return "title" if tool == "create_pull_request" else "message"


def is_trivial(tool: str, args: Dict[str, Any]) -> bool:
    """A small single-file edit (or a docs-only change) may go straight to main. Everything else needs a pull request."""
    if tool == "create_or_update_file":
        path = str(args.get("path") or "").lower()
        return path.endswith(DOC_SUFFIXES) or len(str(args.get("content") or "")) <= TRIVIAL_MAX_CHARS
    if tool == "push_files":
        files = [f for f in (args.get("files") or []) if isinstance(f, dict)]
        if not files:
            return True
        if all(str(f.get("path") or "").lower().endswith(DOC_SUFFIXES) for f in files):
            return True
        return len(files) == 1 and len(str(files[0].get("content") or "")) <= TRIVIAL_MAX_CHARS
    return True


def force_requested(args: Dict[str, Any]) -> bool:
    return any(str(k).lower() in ("force", "force_push", "forced") and bool(v) for k, v in (args or {}).items())


async def repo_state(token: str, owner: str, repo: str) -> Dict[str, Any]:
    """{"default_branch": str|None, "empty": bool}. Any problem returns unknown values (the caller then does not block)."""
    state: Dict[str, Any] = {"default_branch": None, "empty": False}
    if not (token and owner and repo):
        return state
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"https://api.github.com/repos/{owner}/{repo}", headers=headers)
            if r.status_code == 200:
                state["default_branch"] = r.json().get("default_branch")
            c = await client.get(f"https://api.github.com/repos/{owner}/{repo}/commits", headers=headers, params={"per_page": 1})
            if c.status_code == 409:  # "Git Repository is empty."
                state["empty"] = True
    except Exception as e:
        logger.debug(f"[GITHUB_RULES] Could not read repository state: {e}")
    return state


def ticket_denial(tool: str, args: Dict[str, Any], linked: List[Dict[str, Any]]) -> Optional[str]:
    field = message_field(tool)
    if has_ticket(str(args.get(field) or "")):
        return None
    hint = ""
    if linked:
        hint = " Tickets already raised from this channel: " + ", ".join(
            f"{r['issue_key']} ({(r.get('summary') or '')[:50]})" for r in linked[:5]
        ) + "."
    label = "pull request title" if tool == "create_pull_request" else "commit message"
    return (
        f"RULE: this client's {label} must reference the Jira ticket number (for example 'KAN-12: Fix the login button').{hint} "
        f"Put the ticket key at the start of the {field} and call the tool again. If there is no ticket yet, create one first with "
        "jira_create_issue, or ask the user which ticket this belongs to. Only if the user clearly says no ticket is needed, "
        "call again with no_jira_ticket=true."
    )


async def pull_request_denial(tool: str, args: Dict[str, Any], token: str) -> Optional[str]:
    """Blocks direct commits of non-trivial changes to the main branch."""
    if tool not in ("create_or_update_file", "push_files"):
        return None
    branch = str(args.get("branch") or "main").strip() or "main"
    if is_trivial(tool, args):
        return None
    state: Optional[Dict[str, Any]] = None
    if branch.lower() not in PROTECTED_BRANCHES:
        state = await repo_state(token, str(args.get("owner") or ""), str(args.get("repo") or ""))
        if not state.get("default_branch") or branch != state["default_branch"]:
            return None
    else:
        state = await repo_state(token, str(args.get("owner") or ""), str(args.get("repo") or ""))
    if state.get("empty"):
        return None  # the very first commit of a new repository has to go straight in
    return (
        f"RULE: this change is too big to commit straight to '{branch}'. Create a branch first (create_branch, for example "
        "'feature/KAN-12-short-name'), commit the files to that branch, then open a pull request with create_pull_request. "
        "A person reviews and merges it on GitHub. Small single-file edits and documentation changes can still go directly."
    )


def pr_footer(tickets: List[str]) -> str:
    lines = ["", "---", "_Opened by the JTS assistant. A person must review and merge this pull request._"]
    if tickets:
        lines.insert(1, "Jira: " + ", ".join(dict.fromkeys(tickets)))
    return "\n".join(lines)


RULES_PROMPT = (
    "\n\nGITHUB RULES (this client has them on): (1) Write commit messages and pull request titles that say what changed and why, "
    "and start them with the Jira ticket key when the client uses Jira (e.g. 'KAN-12: Fix mobile login button'). "
    "(2) Anything beyond a small single-file edit or a docs change goes on a new branch and into a pull request (create_branch, "
    "commit to the branch, create_pull_request); never commit it straight to main. "
    "(3) You can never force-push or merge: a person reviews and merges pull requests on GitHub. Say so if asked. "
    "(4) Every change still waits for an approval card."
)
