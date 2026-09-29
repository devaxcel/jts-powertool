import io
import unittest
import zipfile
from unittest.mock import patch, MagicMock
from app.file_extractor import (
    extract_text_from_docx,
    extract_text_from_xlsx,
    extract_text_from_pptx,
    extract_text_from_zip,
    extract_generic_text,
    extract_file_content,
)
from app.worker import process_slack_files


class TestFileExtractor(unittest.IsolatedAsyncioTestCase):

    def test_extract_docx(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body>
                <w:p><w:r><w:t>Paragraph 1 of Word document.</w:t></w:r></w:p>
                <w:tbl>
                  <w:tr>
                    <w:tc><w:p><w:r><w:t>Col A</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>Col B</w:t></w:r></w:p></w:tc>
                  </w:tr>
                  <w:tr>
                    <w:tc><w:p><w:r><w:t>Val 1</w:t></w:r></w:p></w:tc>
                    <w:tc><w:p><w:r><w:t>Val 2</w:t></w:r></w:p></w:tc>
                  </w:tr>
                </w:tbl>
              </w:body>
            </w:document>"""
            z.writestr("word/document.xml", doc_xml)

        text = extract_text_from_docx(bio.getvalue())
        self.assertIn("Paragraph 1 of Word document.", text)
        self.assertIn("Col A | Col B", text)
        self.assertIn("Val 1 | Val 2", text)

    def test_extract_xlsx(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            sst_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <si><t>Quarter</t></si>
              <si><t>Revenue</t></si>
              <si><t>Q1</t></si>
            </sst>"""
            z.writestr("xl/sharedStrings.xml", sst_xml)

            sheet_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
              <sheetData>
                <row r="1">
                  <c r="A1" t="s"><v>0</v></c>
                  <c r="B1" t="s"><v>1</v></c>
                </row>
                <row r="2">
                  <c r="A2" t="s"><v>2</v></c>
                  <c r="B2"><v>50000</v></c>
                </row>
              </sheetData>
            </worksheet>"""
            z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        text = extract_text_from_xlsx(bio.getvalue())
        self.assertIn("Quarter | Revenue", text)
        self.assertIn("Q1 | 50000", text)

    def test_extract_pptx(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            slide_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
                   xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
              <a:p><a:r><a:t>Slide Title</a:t></a:r></a:p>
              <a:p><a:r><a:t>Bullet point one</a:t></a:r></a:p>
            </p:sld>"""
            z.writestr("ppt/slides/slide1.xml", slide_xml)

        text = extract_text_from_pptx(bio.getvalue())
        self.assertIn("[Slide 1]", text)
        self.assertIn("Slide Title", text)
        self.assertIn("Bullet point one", text)

    def test_extract_generic_and_doc(self):
        # Plain text
        self.assertEqual(extract_generic_text(b"Hello World"), "Hello World")

        # UTF-16LE text (e.g. from binary .doc)
        binary_doc = b"\xd0\xcf\x11\xe0" + b"\x00" * 30 + "Document Title".encode("utf-16le") + b"\x00" * 20
        text = extract_generic_text(binary_doc)
        self.assertIn("Document Title", text)

    def test_extract_file_content_router(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body><w:p><w:r><w:t>Universal Word Content</w:t></w:r></w:p></w:body>
            </w:document>"""
            z.writestr("word/document.xml", doc_xml)

        text, label = extract_file_content("specs.docx", bio.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertEqual(label, "Word Document")
        self.assertIn("Universal Word Content", text)

    async def test_process_slack_files_docx(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            doc_xml = """<?xml version="1.0" encoding="UTF-8"?>
            <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
              <w:body><w:p><w:r><w:t>Financial Report 2026</w:t></w:r></w:p></w:body>
            </w:document>"""
            z.writestr("word/document.xml", doc_xml)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = bio.getvalue()

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            files = [{
                "name": "report.docx",
                "url_private_download": "https://slack.com/files/report.docx",
                "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "filetype": "docx",
                "size": len(mock_resp.content),
            }]
            blocks, notices = await process_slack_files(files, "xoxb-token")

            self.assertEqual(len(notices), 0)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["type"], "text")
            self.assertIn("Financial Report 2026", blocks[0]["text"])
            self.assertIn("report.docx", blocks[0]["text"])

    async def test_process_slack_files_excel(self):
        bio = io.BytesIO()
        with zipfile.ZipFile(bio, "w") as z:
            sst_xml = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>Total Sales</t></si></sst>"""
            z.writestr("xl/sharedStrings.xml", sst_xml)
            sheet_xml = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c></row></sheetData></worksheet>"""
            z.writestr("xl/worksheets/sheet1.xml", sheet_xml)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = bio.getvalue()

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            files = [{
                "name": "sales.xlsx",
                "url_private_download": "https://slack.com/files/sales.xlsx",
                "mimetype": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "filetype": "xlsx",
                "size": len(mock_resp.content),
            }]
            blocks, notices = await process_slack_files(files, "xoxb-token")
            self.assertEqual(len(notices), 0)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["type"], "text")
            self.assertIn("Total Sales", blocks[0]["text"])

    async def test_process_slack_files_unknown_format_fallback(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.content = b"custom text format line 1\ncustom line 2"

        with patch("httpx.AsyncClient.get", return_value=mock_resp):
            files = [{
                "name": "custom.data",
                "url_private_download": "https://slack.com/files/custom.data",
                "mimetype": "application/octet-stream",
                "filetype": "data",
                "size": len(mock_resp.content),
            }]
            blocks, notices = await process_slack_files(files, "xoxb-token")
            self.assertEqual(len(notices), 0)
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["type"], "text")
            self.assertIn("custom text format line 1", blocks[0]["text"])


if __name__ == "__main__":
    unittest.main()
