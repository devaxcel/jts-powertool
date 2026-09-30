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
