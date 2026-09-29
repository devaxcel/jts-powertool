import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.tools.web_tools import (
    clean_html_to_text,
    fetch_url_content,
    is_safe_url,
    sanitize_url,
    search_web,
)
from app.tools.tool_adapter import ControlledToolAdapter


class TestWebTools(unittest.TestCase):
    def test_sanitize_url(self):
        self.assertEqual(sanitize_url("<https://www.google.com|Google>"), "https://www.google.com")
        self.assertEqual(sanitize_url("<https://www.google.com>"), "https://www.google.com")
        self.assertEqual(sanitize_url("[PLD](https://tutorialspoint.com/pld.htm)"), "https://tutorialspoint.com/pld.htm")
        self.assertEqual(sanitize_url("https://www.google.com)"), "https://www.google.com")
        self.assertEqual(sanitize_url('"https://www.google.com."'), "https://www.google.com")

    def test_is_safe_url_public(self):
        self.assertTrue(is_safe_url("https://www.google.com")[0])
        self.assertTrue(is_safe_url("<https://docs.python.org/3/|Docs>")[0])
        self.assertTrue(is_safe_url("https://docs.python.org/3/")[0])
        self.assertTrue(is_safe_url("http://example.com")[0])

    def test_is_safe_url_blocked(self):
        # Localhost
        self.assertFalse(is_safe_url("http://localhost:8000")[0])
        self.assertFalse(is_safe_url("http://127.0.0.1:8000")[0])
        self.assertFalse(is_safe_url("http://0.0.0.0:8000")[0])

        # AWS metadata
        self.assertFalse(is_safe_url("http://169.254.169.254/latest/meta-data/")[0])

        # Private IP ranges
        self.assertFalse(is_safe_url("http://192.168.1.1")[0])
        self.assertFalse(is_safe_url("http://10.0.0.1")[0])

        # Non-HTTP protocols
        self.assertFalse(is_safe_url("file:///etc/passwd")[0])
        self.assertFalse(is_safe_url("ftp://ftp.example.com")[0])
        self.assertFalse(is_safe_url("javascript:alert(1)")[0])

    def test_clean_html_to_text(self):
        html = """
        <html>
            <head><title>Sample Documentation</title></head>
            <body>
                <script>var x = 10;</script>
                <style>body { color: red; }</style>
                <nav><a href="/home">Home</a></nav>
                <h1>Main Heading</h1>
                <p>This is a test paragraph with a <a href="https://example.com/docs">documentation link</a>.</p>
                <ul>
                    <li>First bullet</li>
                    <li>Second bullet</li>
                </ul>
                <footer>Copyright 2026</footer>
            </body>
        </html>
        """
        title, text = clean_html_to_text(html)
        self.assertEqual(title, "Sample Documentation")
        self.assertIn("# Main Heading", text)
        self.assertIn("[documentation link](https://example.com/docs)", text)
        self.assertIn("- First bullet", text)
        self.assertIn("- Second bullet", text)
        self.assertNotIn("var x = 10", text)
        self.assertNotIn("color: red", text)
        self.assertNotIn("Copyright 2026", text)

    def test_fetch_url_content_blocked(self):
        res = asyncio.run(fetch_url_content("http://127.0.0.1:8000/secret"))
        self.assertFalse(res["success"])
        self.assertIn("URL blocked for security", res["error"])

    def test_search_web_empty(self):
        res = asyncio.run(search_web(""))
        self.assertFalse(res["success"])
        self.assertIn("cannot be empty", res["error"])

    @patch("app.tools.web_tools._search_duckduckgo")
    def test_search_web_mocked(self, mock_ddg):
        mock_ddg.return_value = [
            {"title": "FastAPI Guide", "url": "https://fastapi.tiangolo.com", "snippet": "Modern Python framework"}
        ]
        res = asyncio.run(search_web("fastapi guide", max_results=1))
        self.assertTrue(res["success"])
        self.assertEqual(res["result_count"], 1)
        self.assertIn("FastAPI Guide", res["formatted_summary"])
        self.assertIn("https://fastapi.tiangolo.com", res["formatted_summary"])

    def test_tool_adapter_includes_web_tools(self):
        adapter = ControlledToolAdapter(
            channel_id="C123",
            workspace_id="T123",
            workspace_name="Test Workspace",
        )
        tools = adapter.get_allowed_tools()
        tool_names = [t["name"] for t in tools]
        self.assertIn("fetch_url_content", tool_names)
        self.assertIn("search_web", tool_names)

    def test_tool_adapter_execute_web_tools(self):
        adapter = ControlledToolAdapter(
            channel_id="C123",
            workspace_id="T123",
            workspace_name="Test Workspace",
        )
        # Blocked SSRF URL execution test via adapter
        out, is_err = asyncio.run(
            adapter.execute_tool("fetch_url_content", {"url": "http://127.0.0.1:8000"})
        )
        self.assertTrue(is_err)
        self.assertIn("URL blocked for security", out)


if __name__ == "__main__":
    unittest.main()
