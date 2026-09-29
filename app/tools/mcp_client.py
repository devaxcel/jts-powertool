import asyncio
import logging
import os
import shutil
from typing import Any, Dict, List, Optional, Tuple
from contextlib import asynccontextmanager

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from app.tools.secrets_manager import get_secret

logger = logging.getLogger(__name__)


class GitHubMCPClient:
    """
    Manages connection to the official @modelcontextprotocol/server-github MCP server
    over stdio transport.
    """

    def __init__(self, token: Optional[str] = None):
        self.token = token or get_secret("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        self._session: Optional[ClientSession] = None
        self._read_stream = None
        self._write_stream = None
        self._cm = None

    def _get_npx_executable(self) -> str:
        """Finds npx executable across platforms (Windows / Linux / macOS)."""
        executable = shutil.which("npx") or shutil.which("npx.cmd") or "npx"
        return executable

    def is_configured(self) -> bool:
        """Returns True if GitHub token is present."""
        return bool(self.token and len(self.token.strip()) > 5)

    @asynccontextmanager
    async def connect(self):
        """
        Async context manager that spawns the GitHub MCP server and yields an active ClientSession.
        """
        if not self.is_configured():
            raise ValueError(
                "GITHUB_PERSONAL_ACCESS_TOKEN is missing or not configured. "
                "Please set GITHUB_PERSONAL_ACCESS_TOKEN in .env or AWS Secrets Manager."
            )

        npx_cmd = self._get_npx_executable()
        env = {
            **os.environ,
            "GITHUB_PERSONAL_ACCESS_TOKEN": self.token.strip(),
            "GITHUB_TOKEN": self.token.strip(),
        }

        server_params = StdioServerParameters(
            command=npx_cmd,
            args=["-y", "@modelcontextprotocol/server-github"],
            env=env,
        )

        logger.info("Connecting to GitHub MCP server via stdio (npx -y @modelcontextprotocol/server-github)...")
        async with stdio_client(server_params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                logger.info("GitHub MCP server session initialized successfully.")
                yield session

    async def list_available_tools(self) -> List[Any]:
        """Lists all tools reported by the GitHub MCP server."""
        async with self.connect() as session:
            tools_result = await session.list_tools()
            return tools_result.tools

    async def _call_mcp_tool_safe(self, session: Any, tool_name: str, arguments: Dict[str, Any]) -> Tuple[str, bool]:
        """Safely executes session.call_tool and handles exceptions/errors without crashing."""
        try:
            result = await session.call_tool(tool_name, arguments=arguments)
            parts = []
            if hasattr(result, "content") and result.content:
                for item in result.content:
                    if hasattr(item, "text"):
                        parts.append(item.text)
                    elif isinstance(item, dict) and "text" in item:
                        parts.append(item["text"])
                    else:
                        parts.append(str(item))
            output_text = "\n".join(parts) if parts else "Tool executed with no output."
            is_err = getattr(result, "isError", False)
            return output_text, is_err
        except Exception as e:
            return str(e), True

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """
        Executes a tool on the GitHub MCP server and returns text output.
        Auto-initializes empty repositories with a README.md if 'Git Repository is empty' is encountered.
        Handles 'Reference already exists' as a non-fatal success for branch creation.
        """
        async with self.connect() as session:
            output_text, is_err = await self._call_mcp_tool_safe(session, tool_name, arguments)

            # 1. Auto-handle empty repository error for write / branch / file tools
            if is_err and ("Git Repository is empty" in output_text or "repository is empty" in output_text.lower()):
                owner = arguments.get("owner")
                repo = arguments.get("repo")
                default_repo_str = get_secret("GITHUB_DEFAULT_REPO", "AbdulAleemDev/jts-powertool")
                
                if repo and "/" in str(repo):
                    parts = str(repo).split("/", 1)
                    owner = parts[0].strip()
                    repo = parts[1].strip()
                elif not owner and "/" in default_repo_str:
                    owner = default_repo_str.split("/")[0].strip()

                if not repo and "/" in default_repo_str:
                    repo = default_repo_str.split("/")[1].strip()

                if owner and repo and arguments.get("path") != "README.md":
                    logger.info(f"[AUTO_INIT] Empty repository detected on {owner}/{repo}. Creating initial README.md on main branch...")
                    init_args = {
                        "owner": owner,
                        "repo": repo,
                        "path": "README.md",
                        "content": f"# {repo}\n\nInitial repository setup.",
                        "message": "Initial commit",
                        "branch": "main",
                    }
                    try:
                        init_out, init_err = await self._call_mcp_tool_safe(session, "create_or_update_file", init_args)
                        if not init_err:
                            logger.info(f"[AUTO_INIT] Initial README.md created on {owner}/{repo}. Retrying '{tool_name}'...")
                            output_text, is_err = await self._call_mcp_tool_safe(session, tool_name, arguments)
                    except Exception as err:
                        logger.error(f"[AUTO_INIT] Exception during auto-init: {err}")

            # 2. Handle existing branch as a clean success
            if is_err and tool_name == "create_branch" and ("Reference already exists" in output_text or "already exists" in output_text.lower()):
                branch_name = arguments.get("branch", "target branch")
                logger.info(f"[MCP_CLIENT] Branch '{branch_name}' already exists. Returning success status.")
                return f"Branch '{branch_name}' already exists in repository and is ready for use."

            if is_err:
                return f"[GitHub MCP Error]: {output_text}"

            return output_text
