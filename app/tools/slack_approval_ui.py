import json
from typing import Any, Dict, List, Optional


def build_approval_card_blocks(
    approval_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    default_repo: str = "devaxcel/jts-powertool",
) -> List[Dict[str, Any]]:
    """
    Constructs an interactive Slack Block Kit card requesting human approval
    before executing a GitHub write/update action.
    """
    if tool_name == "publish_website":
        return _build_publish_website_card(approval_id, tool_args)
    if tool_name == "update_website":
        return _build_update_website_card(approval_id, tool_args)
    if tool_name == "call_client_api":
        return _build_client_api_card(approval_id, tool_args)

    owner = tool_args.get("owner", "")
    repo = tool_args.get("repo", "")
    full_repo = f"{owner}/{repo}" if (owner and repo) else default_repo

    target = (
        tool_args.get("path")
        or tool_args.get("branch")
        or (f"Issue #{tool_args.get('issue_number')}" if tool_args.get("issue_number") else "")
        or tool_args.get("title")
        or "Repository"
    )

    message_or_title = (
        tool_args.get("message")
        or tool_args.get("title")
        or tool_args.get("body", "")[:60]
        or "N/A"
    )

    content_preview = (
        tool_args.get("content")
        or tool_args.get("body")
        or (json.dumps(tool_args.get("files", []), indent=2) if "files" in tool_args else "")
    )

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "GitHub Write Permission Request",
                "emoji": False,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repository:*\n`{full_repo}`"},
                {"type": "mrkdwn", "text": f"*Target:*\n`{target}`"},
                {"type": "mrkdwn", "text": f"*Operation:*\n`{tool_name}`"},
                {"type": "mrkdwn", "text": f"*Commit / Title:*\n_{message_or_title}_"},
            ],
        },
    ]

    if content_preview:
        preview_snippet = content_preview.strip()
        if len(preview_snippet) > 350:
            preview_snippet = preview_snippet[:350] + "\n... [Click 'View Full Diff' for complete content]"
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Proposed Content / Changes:*\n```{preview_snippet}```",
            },
        })

    blocks.extend([
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"approval_actions_{approval_id}",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Approve & Apply",
                        "emoji": False,
                    },
                    "style": "primary",
                    "action_id": "approve_github_action",
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Full Diff",
                        "emoji": False,
                    },
                    "action_id": "inspect_github_diff",
                    "value": approval_id,
                },
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "Reject",
                        "emoji": False,
                    },
                    "style": "danger",
                    "action_id": "reject_github_action",
                    "value": approval_id,
                },
            ],
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Proposal ID: `{approval_id}` - Requires human confirmation before committing to GitHub",
                }
            ],
        },
    ])

    return blocks


def build_approved_card_blocks(
    approval_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    approved_by: str,
    execution_result: str = "",
    default_repo: str = "devaxcel/jts-powertool",
) -> List[Dict[str, Any]]:
    """
    Mutates the approval card in-place to remove action buttons and display
    a permanent audit badge with execution results.
    """
    if tool_name == "call_client_api":
        return _client_api_result_blocks(approval_id, tool_args, f"Approved by <@{approved_by}>" if approved_by else "Approved",
                                         execution_result)
    owner = tool_args.get("owner", "")
    repo = tool_args.get("repo", "")
    full_repo = f"{owner}/{repo}" if (owner and repo) else default_repo

    target = (
        tool_args.get("path")
        or tool_args.get("branch")
        or (f"Issue #{tool_args.get('issue_number')}" if tool_args.get("issue_number") else "")
        or tool_args.get("title")
        or "Repository"
    )

    approver_mention = f"<@{approved_by}>" if approved_by else "Authorized User"

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "GitHub Write Permission - Applied",
                "emoji": False,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repository:*\n`{full_repo}`"},
                {"type": "mrkdwn", "text": f"*Target:*\n`{target}`"},
                {"type": "mrkdwn", "text": f"*Operation:*\n`{tool_name}`"},
                {"type": "mrkdwn", "text": f"*Status:*\n*APPROVED & APPLIED*"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Approved by:* {approver_mention}\n*GitHub Action Result:*\n```{execution_result[:500] or 'Successfully executed via GitHub MCP.'}```",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Proposal ID: `{approval_id}` - Audit locked - Changes committed",
                }
            ],
        },
    ]

    return blocks


def build_rejected_card_blocks(
    approval_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    rejected_by: str,
    default_repo: str = "devaxcel/jts-powertool",
) -> List[Dict[str, Any]]:
    """
    Mutates the approval card in-place to remove action buttons and display
    a cancelled audit badge.
    """
    if tool_name == "call_client_api":
        return _client_api_result_blocks(approval_id, tool_args, f"Rejected by <@{rejected_by}>. Nothing was sent.", "")
    owner = tool_args.get("owner", "")
    repo = tool_args.get("repo", "")
    full_repo = f"{owner}/{repo}" if (owner and repo) else default_repo

    target = (
        tool_args.get("path")
        or tool_args.get("branch")
        or (f"Issue #{tool_args.get('issue_number')}" if tool_args.get("issue_number") else "")
        or tool_args.get("title")
        or "Repository"
    )

    rejecter_mention = f"<@{rejected_by}>" if rejected_by else "User"

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "GitHub Write Permission - Cancelled",
                "emoji": False,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repository:*\n`{full_repo}`"},
                {"type": "mrkdwn", "text": f"*Target:*\n`{target}`"},
                {"type": "mrkdwn", "text": f"*Operation:*\n`{tool_name}`"},
                {"type": "mrkdwn", "text": f"*Status:*\n*REJECTED*"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Rejected by:* {rejecter_mention}\n*No changes were committed or made to GitHub.*",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Proposal ID: `{approval_id}` - Request cancelled",
                }
            ],
        },
    ]

    return blocks


def build_expired_card_blocks(
    approval_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
    default_repo: str = "devaxcel/jts-powertool",
) -> List[Dict[str, Any]]:
    """
    Mutates the approval card in-place to remove action buttons and display
    an expired audit badge.
    """
    owner = tool_args.get("owner", "")
    repo = tool_args.get("repo", "")
    full_repo = f"{owner}/{repo}" if (owner and repo) else default_repo

    target = (
        tool_args.get("path")
        or tool_args.get("branch")
        or (f"Issue #{tool_args.get('issue_number')}" if tool_args.get("issue_number") else "")
        or tool_args.get("title")
        or "Repository"
    )

    blocks: List[Dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": "GitHub Write Permission - Expired",
                "emoji": False,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Repository:*\n`{full_repo}`"},
                {"type": "mrkdwn", "text": f"*Target:*\n`{target}`"},
                {"type": "mrkdwn", "text": f"*Operation:*\n`{tool_name}`"},
                {"type": "mrkdwn", "text": f"*Status:*\n*EXPIRED*"},
            ],
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*This approval request has expired.* No changes were committed to GitHub.",
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Proposal ID: `{approval_id}` - Request expired",
                }
            ],
        },
    ]

    return blocks


def build_diff_modal_view(
    approval_id: str,
    tool_name: str,
    tool_args: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Builds the Slack Modal payload for views.open when user clicks 'View Full Diff'.
    """
    target = (
        tool_args.get("path")
        or tool_args.get("branch")
        or (f"Issue #{tool_args.get('issue_number')}" if tool_args.get("issue_number") else "")
        or tool_args.get("title")
        or "Repository"
    )

    content = (
        tool_args.get("content")
        or tool_args.get("body")
        or (json.dumps(tool_args.get("files", []), indent=2) if "files" in tool_args else "")
        or json.dumps(tool_args, indent=2)
    )

    # Slack modal text block max 3000 chars
    if len(content) > 2800:
        content = content[:2800] + "\n\n... [Content truncated for modal display]"

    return {
        "type": "modal",
        "title": {
            "type": "plain_text",
            "text": "Proposed Changes",
            "emoji": True,
        },
        "close": {
            "type": "plain_text",
            "text": "Close",
            "emoji": True,
        },
        "blocks": [
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Operation:* `{tool_name}`"},
                    {"type": "mrkdwn", "text": f"*Target:* `{target}`"},
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Full Content Preview:*\n```{content}```",
                },
            },
        ],
    }


def _build_publish_website_card(approval_id: str, tool_args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Approval card for publishing a website draft: repo, visibility, file list, preview button."""
    files = tool_args.get("files") or []
    listed = "\n".join(f"• `{f.get('path')}` ({int(f.get('size_bytes', 0)) / 1000:.1f} KB)" for f in files[:15])
    if len(files) > 15:
        listed += f"\n… and {len(files) - 15} more"
    repo = f"{tool_args.get('owner', '')}/{tool_args.get('repo', '')}"
    visibility = "Private" if tool_args.get("private") else "Public"
    elements: List[Dict[str, Any]] = []
    if tool_args.get("preview_url"):
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Preview site", "emoji": False},
            "url": tool_args["preview_url"],
            "action_id": "preview_website_draft",
        })
    elements.extend([
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Approve & Publish", "emoji": False},
            "style": "primary",
            "action_id": "approve_github_action",
            "value": approval_id,
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "Reject", "emoji": False},
            "style": "danger",
            "action_id": "reject_github_action",
            "value": approval_id,
        },
    ])
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Publish a website", "emoji": False}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*New repository:*\n`{repo}`"},
                {"type": "mrkdwn", "text": f"*Visibility:*\n{visibility}"},
                {"type": "mrkdwn", "text": f"*Files:*\n{tool_args.get('file_count', len(files))} ({int(tool_args.get('total_bytes', 0)) / 1000:.0f} KB)"},
                {"type": "mrkdwn", "text": f"*Stack / hosting:*\n{tool_args.get('stack_label', 'Static site')} · " + {
                    "static": "GitHub Pages (live)",
                    "pages_build": "built by GitHub, then GitHub Pages (live)",
                    "code_only": "code only: needs PHP hosting",
                }.get(tool_args.get("hosting", "static"), "GitHub Pages")},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": listed or "_No files_"}},
        {"type": "divider"},
        {"type": "actions", "block_id": f"approval_actions_{approval_id}", "elements": elements},
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Proposal ID: `{approval_id}` · Approving creates the repository in the client's GitHub"
                + (" with the code (not live until it's on PHP hosting)." if tool_args.get("hosting") == "code_only" else " and publishes the site."),
            }],
        },
    ]


def _build_update_website_card(approval_id: str, tool_args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Approval card for changing a published website (applied as a pull request that is merged on approval)."""
    changes = tool_args.get("changes") or {}
    rows: List[str] = []
    for label, key in (("added", "added"), ("changed", "modified"), ("removed", "deleted")):
        rows.extend(f"• {label} `{p}`" for p in (changes.get(key) or []))
    listed = "\n".join(rows[:15]) + (f"\n… and {len(rows) - 15} more" if len(rows) > 15 else "")
    elements: List[Dict[str, Any]] = []
    if tool_args.get("preview_url"):
        elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Preview site", "emoji": False},
            "url": tool_args["preview_url"],
            "action_id": "preview_website_draft",
        })
    elements.extend([
        {"type": "button", "text": {"type": "plain_text", "text": "View Changes", "emoji": False},
         "action_id": "inspect_github_diff", "value": approval_id},
        {"type": "button", "text": {"type": "plain_text", "text": "Approve & Merge", "emoji": False},
         "style": "primary", "action_id": "approve_github_action", "value": approval_id},
        {"type": "button", "text": {"type": "plain_text", "text": "Reject", "emoji": False},
         "style": "danger", "action_id": "reject_github_action", "value": approval_id},
    ])
    summary = (tool_args.get("summary") or "").strip()
    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Update a website", "emoji": False}},
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Website:*\n`{tool_args.get('owner', '')}/{tool_args.get('repo', '')}`"},
                {"type": "mrkdwn", "text": f"*Change:*\n{tool_args.get('title', '')}"},
            ],
        },
    ]
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary[:500]}})
    blocks.extend([
        {"type": "section", "text": {"type": "mrkdwn", "text": listed or "_No file changes_"}},
        {"type": "divider"},
        {"type": "actions", "block_id": f"approval_actions_{approval_id}", "elements": elements},
        {
            "type": "context",
            "elements": [{
                "type": "mrkdwn",
                "text": f"Proposal ID: `{approval_id}` · Approving opens a pull request in the client's repository and merges it.",
            }],
        },
    ])
    return blocks


def _client_api_summary(tool_args: Dict[str, Any]) -> List[Dict[str, Any]]:
    body = tool_args.get("body")
    body_text = json.dumps(body, indent=2) if isinstance(body, (dict, list)) else str(body or "")
    if len(body_text) > 600:
        body_text = body_text[:600] + "\n..."
    blocks: List[Dict[str, Any]] = [{
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Service:*\n{tool_args.get('service', '')}"},
            {"type": "mrkdwn", "text": f"*Request:*\n`{tool_args.get('method', '')} {tool_args.get('url', '')}`"},
        ],
    }]
    if tool_args.get("reason"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Why:* {tool_args['reason']}"}})
    if body_text:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Data sent:*\n```{body_text}```"}})
    return blocks


def _build_client_api_card(approval_id: str, tool_args: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Approval card for a change request to one of the client's own services (key added only after approval)."""
    return [
        {"type": "header", "text": {"type": "plain_text", "text": "Approve a change in a connected service", "emoji": False}},
        *_client_api_summary(tool_args),
        {"type": "divider"},
        {
            "type": "actions",
            "block_id": f"approval_actions_{approval_id}",
            "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "Approve & Send", "emoji": False},
                 "style": "primary", "action_id": "approve_github_action", "value": approval_id},
                {"type": "button", "text": {"type": "plain_text", "text": "Reject", "emoji": False},
                 "style": "danger", "action_id": "reject_github_action", "value": approval_id},
            ],
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": f"Proposal ID: `{approval_id}` · The client's saved key is added only when this is approved."}]},
    ]


def _client_api_result_blocks(approval_id: str, tool_args: Dict[str, Any], status: str, result: str) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": "Connected service request", "emoji": False}},
        *_client_api_summary(tool_args),
        {"type": "section", "text": {"type": "mrkdwn", "text": status}},
    ]
    if result:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Result:*\n```{result[:500]}```"}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"Proposal ID: `{approval_id}`"}]})
    return blocks
