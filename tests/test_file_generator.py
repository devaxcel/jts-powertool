import io
import unittest
import zipfile
from unittest.mock import patch, MagicMock
from app.file_generator import (
    generate_pdf,
    generate_pure_pdf,
    generate_docx,
    generate_pure_docx,
    generate_xlsx,
    generate_csv,
    generate_pptx,
    generate_file_bytes,
    extract_file_generation_requests,
    strip_file_generation_blocks,
    format_text_for_slack,
    upload_file_to_slack,
)


class TestFileGenerator(unittest.IsolatedAsyncioTestCase):

    def test_generate_pdf_structure(self):
        content = "# Summary Document\nThis is a test summary.\n- Bullet 1\n- Bullet 2"
        pdf_bytes = generate_pdf(content, title="Test Summary")
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 200)

    def test_generate_pure_pdf_fallback(self):
        content = "Line 1\nLine 2\nLine 3"
        pdf_bytes = generate_pure_pdf(content, title="Fallback PDF")
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 200)

    def test_generate_docx_structure(self):
        content = "# Project Proposal\nOverview paragraph.\n- Requirement A\n- Requirement B"
        docx_bytes = generate_docx(content, title="Proposal")
        self.assertGreater(len(docx_bytes), 500)
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            self.assertIn("word/document.xml", z.namelist())

    def test_generate_pure_docx_fallback(self):
        content = "# Pure Document\nText here\n- Item 1"
        docx_bytes = generate_pure_docx(content, title="Pure Title")
        self.assertGreater(len(docx_bytes), 300)
        with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
            self.assertIn("word/document.xml", z.namelist())

    def test_generate_xlsx_structure(self):
        content = "Name,Department,Salary\nAlice,Engineering,120000\nBob,Design,95000"
        xlsx_bytes = generate_xlsx(content, title="Employees")
        self.assertGreater(len(xlsx_bytes), 300)
        with zipfile.ZipFile(io.BytesIO(xlsx_bytes)) as z:
            self.assertIn("xl/workbook.xml", z.namelist())
            self.assertIn("xl/worksheets/sheet1.xml", z.namelist())

    def test_generate_pptx_structure(self):
        content = "# Slide 1: Welcome\n- Point A\n- Point B\n---\n# Slide 2: Next Steps\n- Action 1"
        pptx_bytes = generate_pptx(content, title="Deck")
        self.assertGreater(len(pptx_bytes), 300)
        with zipfile.ZipFile(io.BytesIO(pptx_bytes)) as z:
            self.assertIn("ppt/presentation.xml", z.namelist())
            self.assertIn("ppt/slides/slide1.xml", z.namelist())

    def test_extract_and_strip_blocks(self):
        text = (
            "Here is your generated file:\n"
            "```generate_file:resume_summary.pdf\n"
            "# Abdul Aleem\n"
            "Full Stack Developer\n"
            "```\n"
            "Let me know if you need any adjustments!"
        )
        reqs = extract_file_generation_requests(text)
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0][0], "resume_summary.pdf")
        self.assertIn("Abdul Aleem", reqs[0][1])

        cleaned = strip_file_generation_blocks(text)
        self.assertNotIn("```generate_file", cleaned)
        self.assertIn("📎 *Generated file:* `resume_summary.pdf`", cleaned)

    def test_format_text_for_slack(self):
        user_example = (
            "**Avoid sharing sensitive codes** in chat messages or public channels - even in a Slack workspace\n"
            "2. **Use secure credential management** - consider storing such information in:\n"
            "   - Password managers (like 1Password, LastPass, etc.)\n"
            "   - GitHub Secrets (for code-related credentials)\n"
            "   - Environment variables (for development)\n"
            "   - Secure vaults or encrypted storage\n"
            "3. **Never commit credentials to repositories** - this is a critical security vulnerability"
        )
        expected = (
            "*Avoid sharing sensitive codes* in chat messages or public channels - even in a Slack workspace\n"
            "2. *Use secure credential management* - consider storing such information in:\n"
            "   - Password managers (like 1Password, LastPass, etc.)\n"
            "   - GitHub Secrets (for code-related credentials)\n"
            "   - Environment variables (for development)\n"
            "   - Secure vaults or encrypted storage\n"
            "3. *Never commit credentials to repositories* - this is a critical security vulnerability"
        )
        self.assertEqual(format_text_for_slack(user_example), expected)

        # Test code blocks are preserved intact
        code_block = "Here is some code:\n```python\nx = 2 ** 8\n**not_bold**\n```\nAnd **bold text** outside."
        expected_code = "Here is some code:\n```python\nx = 2 ** 8\n**not_bold**\n```\nAnd *bold text* outside."
        self.assertEqual(format_text_for_slack(code_block), expected_code)

        # Test inline code preservation
        inline_code = "Use `**kwargs` in python and **important note** here."
        expected_inline = "Use `**kwargs` in python and *important note* here."
        self.assertEqual(format_text_for_slack(inline_code), expected_inline)

        # Test bold-italic
        bold_italic = "This is ***very important*** text."
        expected_bi = "This is *_very important_* text."
        self.assertEqual(format_text_for_slack(bold_italic), expected_bi)

    async def test_upload_file_to_slack_mock(self):
        mock_get_url = MagicMock()
        mock_get_url.json.return_value = {
            "ok": True,
            "upload_url": "https://files.slack.com/upload/v1/mock",
            "file_id": "F12345",
        }
        mock_post_bytes = MagicMock()
        mock_post_bytes.status_code = 200

        mock_complete = MagicMock()
        mock_complete.json.return_value = {"ok": True, "files": [{"id": "F12345"}]}

        with patch("httpx.AsyncClient.post") as mock_post:
            mock_post.side_effect = [mock_get_url, mock_post_bytes, mock_complete]

            success = await upload_file_to_slack(
                token="xoxb-mock",
                channel_id="C123",
                thread_ts="1700.1",
                filename="test.pdf",
                file_bytes=b"%PDF-mock",
            )
            self.assertTrue(success)


if __name__ == "__main__":
    unittest.main()
