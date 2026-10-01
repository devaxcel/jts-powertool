import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.tools.mcp_client import GitHubMCPClient
from app.tools.secrets_manager import get_secret
from app.db.repositories import (
    create_pending_approval,
    update_pending_approval_message_ts,
)
from app.tools.slack_approval_ui import build_approval_card_blocks

logger = logging.getLogger(__name__)

# Tools handled inside JTS PowerTool (no GitHub call, no approval)
LOCAL_TOOLS = {"connect_github"}
# Website builder tools (offered only in build mode). draft_* never touch GitHub; publish_website needs approval.
SITE_TOOLS = {"draft_write_file", "draft_read_file", "draft_list_files", "draft_delete_file", "publish_website"}

# Allowed Read Tools
ALLOWED_READ_TOOLS = {
    "connect_github",
    "get_file_contents",
    "list_issues",
    "get_issue",
    "search_repositories",
    "search_code",
    "search_issues",
    "list_commits",
    "get_commit",
    "list_pull_requests",
    "get_pull_request",
    "get_issue_comments",
    "fetch_url_content",
    "search_web",
}
ALLOWED_READONLY_TOOLS = ALLOWED_READ_TOOLS

# Allowed Write and Update Tools
ALLOWED_WRITE_TOOLS = {
    "create_issue",
    "update_issue",
    "add_issue_comment",
    "create_or_update_file",
    "push_files",
    "create_pull_request",
    "create_branch",
}

# Blocked Destructive Tools
BLOCKED_DESTRUCTIVE_TOOLS = {
    "delete_repository",
}
BLOCKED_WRITE_TOOLS = BLOCKED_DESTRUCTIVE_TOOLS

# Pre-defined schemas for GitHub MCP tools (Anthropic format)
# Enables instantaneous tool declaration to Claude without requiring stdio handshake on every prompt
DEFAULT_GITHUB_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "list_issues",
        "description": "List issues in a GitHub repository with filtering options. Default to the configured repository if owner/repo are not specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {
                    "type": "string",
                    "description": "Repository owner or organization name (optional, defaults to configured repo)",
                },
                "repo": {
                    "type": "string",
                    "description": "Repository name (optional, defaults to configured repo)",
                },
                "state": {
                    "type": "string",
                    "enum": ["open", "closed", "all"],
                    "description": "Filter by issue state (default: 'open')",
                },
                "per_page": {
                    "type": "integer",
                    "description": "Number of issues to return (max 100, default 30)",
                },
            },
        },
    },
    {
        "name": "get_issue",
        "description": "Get detailed information about a specific issue in a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner or org"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "The issue number"},
            },
            "required": ["issue_number"],
        },
    },
    {
        "name": "get_file_contents",
        "description": "Read the contents of a file or directory in a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner or org"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file or directory within repo"},
                "branch": {"type": "string", "description": "Branch name (optional, defaults to default branch)"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_repositories",
        "description": "Search GitHub repositories by query string.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "GitHub search query"},
                "per_page": {"type": "integer", "description": "Results per page (default 10)"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_code",
        "description": "Search code across GitHub repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query terms"},
                "per_page": {"type": "integer", "description": "Results per page (default 10)"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "search_issues",
        "description": "Search issues and pull requests across GitHub repositories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "Search query terms"},
                "per_page": {"type": "integer", "description": "Results per page (default 10)"},
            },
            "required": ["q"],
        },
    },
    {
        "name": "list_commits",
        "description": "List commits for a GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner or org"},
                "repo": {"type": "string", "description": "Repository name"},
                "sha": {"type": "string", "description": "SHA or branch to start listing commits from"},
                "per_page": {"type": "integer", "description": "Results per page (default 20)"},
            },
        },
    },
    {
        "name": "create_issue",
        "description": "Create a new issue in the GitHub repository. Defaults to the configured repository if owner/repo are not specified.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Title of the issue"},
                "body": {"type": "string", "description": "Body content of the issue"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels for the issue"},
                "assignees": {"type": "array", "items": {"type": "string"}, "description": "Usernames to assign"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "update_issue",
        "description": "Update an existing issue (e.g. change title, body, state to closed/open, labels).",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number to update"},
                "title": {"type": "string", "description": "New title"},
                "body": {"type": "string", "description": "New body"},
                "state": {"type": "string", "enum": ["open", "closed"], "description": "Issue state"},
                "labels": {"type": "array", "items": {"type": "string"}, "description": "Labels"},
            },
            "required": ["issue_number"],
        },
    },
    {
        "name": "add_issue_comment",
        "description": "Add a comment to an existing issue or pull request in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "issue_number": {"type": "integer", "description": "Issue number to comment on"},
                "body": {"type": "string", "description": "Comment text"},
            },
            "required": ["issue_number", "body"],
        },
    },
    {
        "name": "create_or_update_file",
        "description": "Create a new file or update an existing file in the GitHub repository. You MUST pass the complete source code/markup in 'content' and a commit message in 'message'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "path": {"type": "string", "description": "Path to file in repository (e.g. 'index.html' or 'src/App.js')"},
                "content": {"type": "string", "description": "The complete source code or file contents to write (e.g. full HTML, CSS, JS, Python, etc.). Cannot be empty."},
                "message": {"type": "string", "description": "Git commit message describing the file creation or update"},
                "branch": {"type": "string", "description": "Branch name (defaults to 'main')"},
                "sha": {"type": "string", "description": "Blob SHA if replacing an existing file"},
            },
            "required": ["path", "content", "message", "branch"],
        },
    },
    {
        "name": "push_files",
        "description": "Commit and push multiple files simultaneously to a GitHub branch.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "Target branch"},
                "files": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "File path"},
                            "content": {"type": "string", "description": "File content"},
                        },
                        "required": ["path", "content"],
                    },
                    "description": "Files to push",
                },
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["branch", "files", "message"],
        },
    },
    {
        "name": "create_pull_request",
        "description": "Create a new pull request in the repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "title": {"type": "string", "description": "Pull request title"},
                "body": {"type": "string", "description": "Pull request description"},
                "head": {"type": "string", "description": "Branch containing new changes"},
                "base": {"type": "string", "description": "Branch to merge changes into (e.g. 'main')"},
            },
            "required": ["title", "head", "base"],
        },
    },
    {
        "name": "create_branch",
        "description": "Create a new branch in the GitHub repository.",
        "input_schema": {
            "type": "object",
            "properties": {
                "owner": {"type": "string", "description": "Repository owner"},
                "repo": {"type": "string", "description": "Repository name"},
                "branch": {"type": "string", "description": "New branch name"},
                "from_branch": {"type": "string", "description": "Source branch (optional)"},
            },
            "required": ["branch"],
        },
    },
    {
        "name": "fetch_url_content",
        "description": "Fetches and reads the full text, documentation, and markdown content of any public URL or webpage. Use this whenever the user asks you to visit, read, check, analyze, or summarize a link/URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The complete HTTP or HTTPS URL to visit and extract content from.",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "search_web",
        "description": "Searches the live internet for search queries, current events, documentation, news, or website links. Use this when the user asks a question requiring fresh web information, search results, or links.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query keywords to search on the web.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Number of search results to return (default: 5, max: 10).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "connect_github",
        "description": (
            "Posts a one-click 'Connect GitHub' button in this Slack conversation so the user can link their own GitHub "
            "account or organization. Use when the user asks to connect/link/add their GitHub, or when a GitHub action "
            "fails because this client hasn't connected GitHub yet. Never ask the user for a GitHub token or password."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


SITE_TOOL_SCHEMAS = [
    {
        "name": "draft_write_file",
        "description": (
            "Website builder: create or replace ONE file in this conversation's private website draft (nothing goes to "
            "GitHub). Give the complete file content. Paths are relative, e.g. 'index.html', 'css/styles.css', "
            "'js/main.js'. Allowed: .html .css .js .json .svg .txt .md .xml .webmanifest."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative file path, e.g. 'about.html'"},
                "content": {"type": "string", "description": "The complete file content"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "draft_read_file",
        "description": "Website builder: read one file from this conversation's website draft.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "draft_list_files",
        "description": "Website builder: list the files (and sizes) in this conversation's website draft.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "draft_delete_file",
        "description": "Website builder: remove one file from this conversation's website draft.",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    },
    {
        "name": "publish_website",
        "description": (
            "Website builder: when the draft is complete (has index.html), propose publishing it. This posts ONE approval "
            "card with a preview link. After a human approves, a new repository is created in the client's own GitHub, "
            "all files are committed, and GitHub Pages hosting is turned on. Call it once; don't say the site is live yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_name": {"type": "string", "description": "New repository name, lowercase-with-dashes, e.g. 'sunrise-bakery-site'"},
                "description": {"type": "string", "description": "One-line description of the website"},
                "private": {"type": "boolean", "description": "Private repository (GitHub Pages then needs a paid plan). Default false."},
            },
            "required": ["repo_name"],
        },
    },
]


class ControlledToolAdapter:
    """
    Safety gate between Claude Agent and external GitHub MCP Server.
    Enforces read, write, and update policies, while blocking destructive actions.
    """

    def __init__(
        self,
        mcp_client: Optional[GitHubMCPClient] = None,
        channel_id: str = "",
        thread_ts: str = "",
        user_id: str = "",
        slack_token: str = "",
        require_approval_for_writes: bool = True,
        workspace_id: str = "",
        workspace_name: str = "",
        channel_name: str = "",
        github_context: Optional[Dict[str, Any]] = None,
        build_mode: bool = False,
    ):
        from app.services.channel_secrets_service import canonical_channel_id
        self.mcp_client = mcp_client or GitHubMCPClient()
        self.github_context = github_context or {}
        self.build_mode = build_mode
        if self.github_context.get("source") == "client_app":
            # The client's own GitHub: only their chosen default repo (may be none -> the user must name one).
            self.default_repo = self.github_context.get("default_repo") or ""
        else:
            self.default_repo = get_secret("GITHUB_DEFAULT_REPO", "devaxcel/jts-powertool")
        self.workspace_id = workspace_id
        self.workspace_name = workspace_name
        self.channel_name = channel_name
        self.channel_id = canonical_channel_id(
            channel_id,
            workspace_id=workspace_id,
            workspace_name=workspace_name,
            channel_name=channel_name,
        )
        self.thread_ts = thread_ts
        self.user_id = user_id
        self.slack_token = slack_token or get_secret("SLACK_BOT_TOKEN", "")
        self.require_approval_for_writes = require_approval_for_writes
        self.approval_card_posted: bool = False

    async def _submit_for_approval(self, tool_name: str, effective_args: Dict[str, Any]) -> Tuple[str, bool]:
        """Stores a pending approval and posts the interactive approval card in Slack."""
        approval_id = f"appr_{uuid.uuid4().hex[:12]}"
        create_pending_approval(
            approval_id=approval_id,
            channel_id=self.channel_id,
            thread_ts=self.thread_ts,
            user_id=self.user_id,
            tool_name=tool_name,
            tool_arguments=effective_args,
            channel_name=self.channel_name,
        )
        logger.info(
            f"[DEBUG_TOOL] Pending approval created in DB: approval_id='{approval_id}', tool='{tool_name}', "
            f"channel='{self.channel_id}', channel_name='{self.channel_name}', user='{self.user_id}'"
        )

        # Post Block Kit Approval Card to Slack if Slack context is present
        if self.channel_id and self.slack_token:
            try:
                blocks = build_approval_card_blocks(
                    approval_id=approval_id,
                    tool_name=tool_name,
                    tool_args=effective_args,
                    default_repo=self.default_repo,
                )
                target_thread = (
                    self.thread_ts
                    if (self.thread_ts and not self.thread_ts.startswith("channel_") and not self.thread_ts.startswith("dm_"))
                    else None
                )
                logger.info(
                    f"[DEBUG_SLACK] Posting approval card to Slack: channel='{self.channel_id}', "
                    f"thread='{target_thread}', approval_id='{approval_id}', total_blocks={len(blocks)}"
                )
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        "https://slack.com/api/chat.postMessage",
                        headers={"Authorization": f"Bearer {self.slack_token}"},
                        json={
                            "channel": self.channel_id,
                            "thread_ts": target_thread,
                            "text": f"🛡️ GitHub Write Permission Request: `{tool_name}`",
                            "blocks": blocks,
                        }
                    )
                    resp_data = resp.json()
                    resp_ok = resp_data.get("ok", False)
                    resp_err = resp_data.get("error")
                    card_ts = resp_data.get("ts", "")
                    logger.info(
                        f"[DEBUG_SLACK] chat.postMessage response: ok={resp_ok}, error={resp_err}, "
                        f"card_ts='{card_ts}', status_code={resp.status_code}"
                    )
                    if resp_ok:
                        self.approval_card_posted = True
                        update_pending_approval_message_ts(approval_id, card_ts)
                        logger.info(
                            f"[DEBUG_SLACK] approval_card_posted set to True for approval_id='{approval_id}'"
                        )
                    else:
                        logger.error(
                            f"[DEBUG_SLACK] FAILED to post approval card to Slack: error='{resp_err}', "
                            f"approval_id='{approval_id}'"
                        )
            except Exception as e:
                logger.error(f"[DEBUG_SLACK] Exception posting approval card: {e}", exc_info=True)

        return (
            f"Action proposal for '{tool_name}' has been prepared and submitted for human approval. "
            "An interactive approval card has been posted to this Slack conversation with [Approve & Apply], [View Full Diff], and [Reject] buttons. "
            "The changes will be applied to GitHub immediately upon user approval.",
            False,
        )

    async def _run_site_tool(self, tool_name: str, args: Dict[str, Any]) -> Tuple[str, bool]:
        """Website builder tools. Drafts live in the database; only publish_website (after approval) touches GitHub."""
        from app.services import site_builder_service as sb
        from app.services.channel_secrets_service import get_folder_id_for_channel

        folder_id = get_folder_id_for_channel(self.channel_id)
        if not folder_id:
            return (
                "Websites can only be built in a channel that belongs to a client. Tell the user to ask their JTS "
                "administrator to add this channel to their client.",
                True,
            )
        thread_key = self.thread_ts or f"channel_{self.channel_id}"
        try:
            if tool_name == "draft_write_file":
                draft = sb.get_or_create_draft(self.channel_id, thread_key, folder_id, self.user_id)
                res = sb.write_file(draft["id"], args.get("path", ""), args.get("content"))
                return (
                    f"Saved {res['path']} ({res['size_bytes']:,} bytes). Draft now has {res['file_count']} files "
                    f"({res['total_bytes']:,} bytes).",
                    False,
                )

            draft = sb.get_open_draft(self.channel_id, thread_key)
            if not draft:
                return ("There's no website draft in this conversation yet. Start with draft_write_file('index.html', ...).", True)

            if tool_name == "draft_list_files":
                files = sb.list_files(draft["id"])
                if not files:
                    return ("The draft is empty.", False)
                lines = "\n".join(f"- {f['path']} ({f['size_bytes']:,} bytes)" for f in files)
                return (f"Draft files ({len(files)}):\n{lines}", False)

            if tool_name == "draft_read_file":
                content = sb.read_file(draft["id"], args.get("path", ""))
                return (content, False) if content is not None else (f"'{args.get('path')}' isn't in the draft.", True)

            if tool_name == "draft_delete_file":
                removed = sb.delete_file(draft["id"], args.get("path", ""))
                return ("Removed." if removed else f"'{args.get('path')}' wasn't in the draft.", not removed)

            if tool_name == "publish_website":
                proposal = sb.build_publish_proposal(
                    draft,
                    str(args.get("repo_name", "")).strip(),
                    str(args.get("description", "") or ""),
                    bool(args.get("private", False)),
                )
                out, is_err = await self._submit_for_approval("publish_website", proposal)
                if not is_err:
                    sb.set_status(draft["id"], "pending_approval")
                    out = (
                        f"Publishing was proposed: repository {proposal['owner']}/{proposal['repo']} with "
                        f"{proposal['file_count']} files. An approval card with a 'Preview site' button was posted. "
                        "Tell the user to check the preview and approve; the live link is shared after approval."
                    )
                return (out, is_err)
        except sb.SiteBuilderError as e:
            return (str(e), True)
        except Exception as e:
            logger.error(f"[SITE_BUILDER] Tool {tool_name} failed: {e}", exc_info=True)
            return (f"The website draft couldn't be updated ({e}). Try again.", True)
        return (f"Unknown website tool '{tool_name}'.", True)

    async def _post_connect_github_button(self) -> Tuple[str, bool]:
        """Posts a single-use 'Connect GitHub' button for this channel's client."""
        from app.services import github_app_service as gh
        from app.services.channel_secrets_service import get_folder_id_for_channel

        folder_id = get_folder_id_for_channel(self.channel_id)
        if not folder_id:
            return (
                "This Slack channel isn't linked to a client yet, so GitHub can't be connected here. "
                "Tell the user to ask their JTS administrator to add this channel to their client first.",
                True,
            )
        try:
            url = gh.create_connect_link(folder_id, channel_id=self.channel_id, slack_user=self.user_id)
        except gh.GitHubAppError as e:
            return (str(e), True)

        if not (self.channel_id and self.slack_token):
            return (f"Share this one-time link with the user (valid 15 minutes): {url}", False)

        target_thread = (
            self.thread_ts
            if (self.thread_ts and not self.thread_ts.startswith("channel_") and not self.thread_ts.startswith("dm_"))
            else None
        )
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Connect your GitHub*\n"
                        "Click the button, choose your GitHub account or organization and which "
                        "repositories JTS PowerTool may use, then click *Install* and *Authorize*. "
                        "No tokens or passwords needed. The link works once, for 15 minutes."
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Connect GitHub"},
                        "url": url,
                        "style": "primary",
                        "action_id": "github_connect_link",
                    }
                ],
            },
        ]
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {self.slack_token}"},
                    json={"channel": self.channel_id, "thread_ts": target_thread, "text": "Connect your GitHub", "blocks": blocks},
                )
            if not resp.json().get("ok"):
                raise RuntimeError(resp.json().get("error"))
        except Exception as e:
            logger.warning(f"[GITHUB_APP] Could not post Connect GitHub button: {e}")
            return (f"Share this one-time link with the user (valid 15 minutes): {url}", False)
        return (
            "A 'Connect GitHub' button was posted in the conversation. Tell the user briefly to click it, pick their "
            "account and repositories, then Install and Authorize; you'll confirm here when it's connected. "
            "Do not repeat the link.",
            False,
        )

    def get_allowed_tools(self) -> List[Dict[str, Any]]:
        """
        Returns Anthropic-compatible tool definitions filtered by safety policy.
        Enriched with default repository hints.
        """
        owner = ""
        repo = ""
        if self.default_repo and "/" in self.default_repo:
            owner, repo = self.default_repo.split("/", 1)
            owner, repo = owner.strip(), repo.strip()

        tools = []
        import copy
        for t in DEFAULT_GITHUB_TOOL_SCHEMAS:
            if not self.is_tool_allowed(t["name"]):
                continue

            tool_copy = copy.deepcopy(t)
            props = tool_copy.get("input_schema", {}).get("properties", {})

            if owner and "owner" in props:
                props["owner"]["default"] = owner
                props["owner"]["description"] = f"Repository owner or org (default: '{owner}')"
            if repo and "repo" in props:
                props["repo"]["default"] = repo
                props["repo"]["description"] = f"Repository name (default: '{repo}')"

            if self.default_repo:
                tool_copy["description"] = (
                    f"{tool_copy['description']} "
                    f"Defaults to the team's repository '{self.default_repo}' if not specified. "
                    "Do not ask the user for repository name if asking about our project; call this tool directly."
                )

            tools.append(tool_copy)

        if self.build_mode:
            tools.extend(copy.deepcopy(SITE_TOOL_SCHEMAS))
        return tools

    def is_tool_allowed(self, tool_name: str) -> bool:
        """
        Validates whether a tool is approved for autonomous execution.
        """
        if tool_name in BLOCKED_DESTRUCTIVE_TOOLS:
            return False
        if tool_name in SITE_TOOLS:
            return self.build_mode
        return (tool_name in ALLOWED_READ_TOOLS) or (tool_name in ALLOWED_WRITE_TOOLS)

    def _inject_default_repo(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Injects default owner and repo into arguments if omitted,
        and normalizes 'owner/repo' strings if passed in repo.
        """
        updated = dict(arguments or {})
        if updated.get("repo") and "/" in str(updated["repo"]):
            parts = str(updated["repo"]).split("/", 1)
            updated["owner"] = parts[0].strip()
            updated["repo"] = parts[1].strip()

        if self.default_repo and "/" in self.default_repo:
            default_owner, default_repo_name = self.default_repo.split("/", 1)
            if not updated.get("owner"):
                updated["owner"] = default_owner.strip()
            if not updated.get("repo"):
                updated["repo"] = default_repo_name.strip()
        return updated

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """
        Executes an approved tool via the GitHub MCP Client.
        Returns (result_text, is_error).
        If tool_name is in ALLOWED_WRITE_TOOLS and require_approval_for_writes is True:
        posts an interactive Slack approval card and creates a pending_approval record.
        """
        # 1. Safety verification
        if not self.is_tool_allowed(tool_name):
            logger.warning(f"Controlled Tool Adapter BLOCKED unauthorized tool: {tool_name}")
            return (
                f"Action blocked by Controlled Tool Adapter: Operation '{tool_name}' is not permitted. "
                "Destructive operations like deleting a repository are prohibited.",
                True,
            )

        # 1.5. Native Web Search and URL Fetching tools
        if tool_name == "fetch_url_content":
            url_val = (arguments or {}).get("url", "")
            if not url_val or not str(url_val).strip():
                return ("Error: 'url' parameter is required for fetch_url_content.", True)
            try:
                from app.tools.web_tools import fetch_url_content
                res = await fetch_url_content(str(url_val).strip())
                if res.get("success"):
                    out = f"Webpage Content from '{res.get('url')}' (Title: '{res.get('title')}'):\n\n{res.get('content')}"
                    return (out, False)
                return (f"Failed to fetch webpage content: {res.get('error')}", True)
            except Exception as e:
                logger.error(f"Error in fetch_url_content: {e}", exc_info=True)
                return (f"Error fetching URL: {str(e)}", True)

        elif tool_name == "search_web":
            query_val = (arguments or {}).get("query", "")
            max_res = (arguments or {}).get("max_results", 5)
            if not query_val or not str(query_val).strip():
                return ("Error: 'query' parameter is required for search_web.", True)
            try:
                from app.tools.web_tools import search_web
                res = await search_web(str(query_val).strip(), max_results=max_res)
                if res.get("success"):
                    return (res.get("formatted_summary", "No search results found."), False)
                return (f"Web search failed: {res.get('error')}", True)
            except Exception as e:
                logger.error(f"Error in search_web: {e}", exc_info=True)
                return (f"Error executing web search: {str(e)}", True)

        if tool_name == "connect_github":
            return await self._post_connect_github_button()

        if tool_name in SITE_TOOLS:
            return await self._run_site_tool(tool_name, arguments or {})

        # 1.6. Client GitHub connection policy
        gh_ctx = self.github_context
        if gh_ctx.get("error"):
            return (f"{gh_ctx['error']} Call connect_github to post a new Connect GitHub button.", True)
        if (
            gh_ctx.get("folder_id")
            and gh_ctx.get("source") == "none"
            and str(get_secret("GITHUB_REQUIRE_CLIENT_CONNECTION", "false")).strip().lower() in ("1", "true", "yes")
        ):
            return (
                "This client hasn't connected their GitHub yet. Call connect_github to post a Connect GitHub button, "
                "and tell the user to click it.",
                True,
            )

        # 2. Check configuration
        if not self.mcp_client.is_configured():
            return (
                "GitHub integration is not yet fully configured: GITHUB_PERSONAL_ACCESS_TOKEN is missing. "
                "Please configure GITHUB_PERSONAL_ACCESS_TOKEN in your environment or AWS Secrets Manager.",
                True,
            )

        # 3. Inject repository context defaults
        effective_args = self._inject_default_repo(arguments)
        logger.info(
            f"[DEBUG_TOOL] execute_tool: tool_name='{tool_name}', "
            f"owner='{effective_args.get('owner')}', repo='{effective_args.get('repo')}'"
        )

        # 3.5. Validate required arguments for write tools before human approval
        if tool_name == "create_or_update_file":
            path_val = effective_args.get("path")
            if not path_val or not str(path_val).strip():
                logger.warning(
                    f"[DEBUG_TOOL] Validation FAILED: create_or_update_file missing 'path'. "
                    f"path={repr(path_val)}"
                )
                return (
                    "Error: 'path' parameter is required and cannot be empty for create_or_update_file. "
                    "Please specify the repository file path.",
                    True,
                )
            content_val = effective_args.get("content")
            if not content_val or not str(content_val).strip():
                logger.warning(
                    f"[DEBUG_TOOL] Validation FAILED: create_or_update_file missing/empty 'content'. "
                    f"path='{path_val}', content_len={len(str(content_val or ''))}. Returning tool error."
                )
                return (
                    "Error: 'content' parameter is required and cannot be empty for create_or_update_file. "
                    "You must provide the complete source code or file content in the 'content' argument so it can be committed to GitHub. "
                    "Please call create_or_update_file again with the full file content and commit message.",
                    True,
                )
            logger.info(
                f"[DEBUG_TOOL] Validation PASSED: create_or_update_file with valid path='{path_val}', "
                f"content_len={len(str(content_val))} chars, message='{effective_args.get('message', '')}'"
            )
            if not effective_args.get("message") or not str(effective_args.get("message")).strip():
                effective_args["message"] = f"Create {effective_args.get('path', 'file')}"
            if not effective_args.get("branch") or not str(effective_args.get("branch")).strip():
                effective_args["branch"] = "main"

        elif tool_name == "push_files":
            files_val = effective_args.get("files")
            if not files_val or not isinstance(files_val, list):
                logger.warning(f"[DEBUG_TOOL] Validation FAILED: push_files missing 'files' list.")
                return (
                    "Error: 'files' array is required for push_files. "
                    "You must provide an array of file objects with 'path' and 'content' so they can be committed to GitHub. "
                    "Please call push_files again with the complete files.",
                    True,
                )
            for f in files_val:
                if not isinstance(f, dict) or not f.get("path") or not str(f.get("content", "")).strip():
                    logger.warning(f"[DEBUG_TOOL] Validation FAILED: push_files contains invalid file item.")
                    return (
                        "Error: Each item in 'files' must be an object with 'path' and non-empty 'content'. "
                        "Please call push_files again with the complete file contents.",
                        True,
                    )
            if not effective_args.get("message") or not str(effective_args.get("message")).strip():
                effective_args["message"] = "Commit changes"
            if not effective_args.get("branch") or not str(effective_args.get("branch")).strip():
                effective_args["branch"] = "main"

        elif tool_name == "create_issue":
            if not effective_args.get("title") or not str(effective_args.get("title")).strip():
                logger.warning(f"[DEBUG_TOOL] Validation FAILED: create_issue missing 'title'.")
                return (
                    "Error: 'title' is required and cannot be empty for create_issue. Please provide an issue title.",
                    True,
                )

        elif tool_name == "create_branch":
            if not effective_args.get("branch") or not str(effective_args.get("branch")).strip():
                logger.warning(f"[DEBUG_TOOL] Validation FAILED: create_branch missing 'branch'.")
                return (
                    "Error: 'branch' is required and cannot be empty for create_branch. Please provide a branch name.",
                    True,
                )

        # 4. Human-in-the-loop approval interception for write actions
        if self.require_approval_for_writes and tool_name in ALLOWED_WRITE_TOOLS:
            return await self._submit_for_approval(tool_name, effective_args)

        # 5. Autonomous execution for read actions
        try:
            logger.info(f"Controlled Tool Adapter dispatching '{tool_name}' to GitHub MCP with args: {effective_args}")
            output = await self.mcp_client.execute_tool(tool_name, effective_args)
            return output, False
        except Exception as e:
            logger.error(f"Error executing GitHub MCP tool '{tool_name}': {e}", exc_info=True)
            return f"Error executing tool '{tool_name}': {str(e)}", True
