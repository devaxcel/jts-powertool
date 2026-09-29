import io
import logging
import re
import tarfile
import zipfile
import xml.etree.ElementTree as ET
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def extract_text_from_docx(file_bytes: bytes) -> str:
    """Extract paragraphs and tables from a Word (.docx) document."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            namelist = z.namelist()
            sections = []

            # 1. Main document content
            if "word/document.xml" in namelist:
                tree = ET.fromstring(z.read("word/document.xml"))
                body = next((e for e in tree.iter() if e.tag.endswith("body")), tree)
                for child in body:
                    if child.tag.endswith("tbl"):
                        # Table rows
                        for tr in child:
                            if tr.tag.endswith("tr"):
                                cells = []
                                for tc in tr:
                                    if tc.tag.endswith("tc"):
                                        cell_text = " ".join(
                                            t.text for t in tc.iter() if t.tag.endswith("t") and t.text
                                        ).strip()
                                        cells.append(cell_text)
                                if any(cells):
                                    sections.append(" | ".join(cells))
                    elif child.tag.endswith("p"):
                        # Paragraph text
                        p_texts = [t.text for t in child.iter() if t.tag.endswith("t") and t.text]
                        if p_texts:
                            sections.append("".join(p_texts))

            # 2. Check headers / footers if document.xml was sparse
            if len(sections) < 3:
                for hf in sorted([f for f in namelist if (f.startswith("word/header") or f.startswith("word/footer")) and f.endswith(".xml")]):
                    try:
                        hf_tree = ET.fromstring(z.read(hf))
                        hf_texts = [t.text for t in hf_tree.iter() if t.tag.endswith("t") and t.text]
                        if hf_texts:
                            sections.append("".join(hf_texts))
                    except Exception:
                        pass

            return "\n\n".join(sections).strip()
    except Exception as e:
        logger.warning(f"Error extracting docx text: {e}")
        return ""


def extract_text_from_pptx(file_bytes: bytes) -> str:
    """Extract slide text from a PowerPoint (.pptx) presentation."""
    try:
        slides = []
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            slide_files = sorted(
                [f for f in z.namelist() if f.startswith("ppt/slides/slide") and f.endswith(".xml")],
                key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0
            )
            for i, sf in enumerate(slide_files, 1):
                xml_content = z.read(sf)
                tree = ET.fromstring(xml_content)
                texts = [elem.text for elem in tree.iter() if elem.tag.endswith("t") and elem.text]
                if texts:
                    slides.append(f"[Slide {i}]\n" + "\n".join(texts))
        return "\n\n".join(slides).strip()
    except Exception as e:
        logger.warning(f"Error extracting pptx text: {e}")
        return ""


def extract_text_from_xlsx(file_bytes: bytes) -> str:
    """Extract cell values and shared strings from an Excel (.xlsx/.xlsm) spreadsheet."""
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            namelist = z.namelist()
            shared_strings = []
            if "xl/sharedStrings.xml" in namelist:
                tree = ET.fromstring(z.read("xl/sharedStrings.xml"))
                for si in tree.iter():
                    if si.tag.endswith("si"):
                        si_text = "".join(t.text for t in si.iter() if t.tag.endswith("t") and t.text)
                        shared_strings.append(si_text)

            sheet_files = sorted([f for f in namelist if f.startswith("xl/worksheets/sheet") and f.endswith(".xml")])
            sheet_rows = []
            for sf in sheet_files:
                sheet_name = sf.split("/")[-1].replace(".xml", "")
                tree = ET.fromstring(z.read(sf))
                sheet_rows.append(f"--- Sheet: {sheet_name.capitalize()} ---")
                for row_elem in tree.iter():
                    if row_elem.tag.endswith("row"):
                        row_vals = []
                        for c_elem in row_elem.iter():
                            if c_elem.tag.endswith("c"):
                                t_attr = c_elem.attrib.get("t", "")
                                v_elem = next((child for child in c_elem if child.tag.endswith("v")), None)
                                is_elem = next((child for child in c_elem if child.tag.endswith("is")), None)

                                if is_elem is not None:
                                    inline_text = "".join(t.text for t in is_elem.iter() if t.tag.endswith("t") and t.text)
                                    row_vals.append(inline_text)
                                elif v_elem is not None and v_elem.text:
                                    if t_attr == "s" and v_elem.text.isdigit():
                                        idx = int(v_elem.text)
                                        row_vals.append(shared_strings[idx] if idx < len(shared_strings) else v_elem.text)
                                    elif t_attr == "b":
                                        row_vals.append("TRUE" if v_elem.text == "1" else "FALSE")
                                    else:
                                        row_vals.append(v_elem.text)
                        if row_vals:
                            sheet_rows.append(" | ".join(row_vals))

            if sheet_rows:
                return "\n".join(sheet_rows).strip()
            elif shared_strings:
                return "\n".join(shared_strings).strip()
            return ""
    except Exception as e:
        logger.warning(f"Error extracting xlsx text: {e}")
        return ""


def extract_text_from_zip(file_bytes: bytes) -> str:
    """List files and extract readable text from a ZIP archive."""
    try:
        extracted = []
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
            names = z.namelist()
            extracted.append(f"Archive contents ({len(names)} files):\n" + "\n".join(f"- {n}" for n in names[:50]))
            for n in names[:15]:
                if any(n.lower().endswith(ext) for ext in [".txt", ".md", ".json", ".csv", ".tsv", ".py", ".js", ".ts", ".html", ".xml", ".log", ".yaml", ".yml", ".sql"]):
                    try:
                        content = z.read(n).decode("utf-8", errors="replace")
                        if len(content) > 10000:
                            content = content[:10000] + "\n...[truncated]"
                        extracted.append(f"\n--- File inside archive: {n} ---\n{content}\n--- End of {n} ---")
                    except Exception:
                        pass
        return "\n".join(extracted).strip()
    except Exception as e:
        logger.warning(f"Error reading zip archive: {e}")
        return ""


def extract_text_from_tar(file_bytes: bytes) -> str:
    """List files and extract readable text from a TAR/TAR.GZ archive."""
    try:
        extracted = []
        with tarfile.open(fileobj=io.BytesIO(file_bytes)) as tar:
            members = tar.getmembers()
            extracted.append(f"Archive contents ({len(members)} files):\n" + "\n".join(f"- {m.name}" for m in members[:50]))
            count = 0
            for m in members:
                if m.isfile() and any(m.name.lower().endswith(ext) for ext in [".txt", ".md", ".json", ".csv", ".tsv", ".py", ".js", ".ts", ".html", ".xml", ".log", ".yaml", ".yml", ".sql"]):
                    f = tar.extractfile(m)
                    if f:
                        try:
                            content = f.read().decode("utf-8", errors="replace")
                            if len(content) > 10000:
                                content = content[:10000] + "\n...[truncated]"
                            extracted.append(f"\n--- File inside archive: {m.name} ---\n{content}\n--- End of {m.name} ---")
                            count += 1
                            if count >= 15:
                                break
                        except Exception:
                            pass
        return "\n".join(extracted).strip()
    except Exception as e:
        logger.warning(f"Error reading tar archive: {e}")
        return ""


def extract_text_from_doc(file_bytes: bytes) -> str:
    """Extract textual content from older binary Word 97-2003 (.doc) documents."""
    return extract_generic_text(file_bytes)


def extract_generic_text(file_bytes: bytes) -> str:
    """Universal text extraction: UTF-8, UTF-16, Latin-1, or embedded string sequences."""
    if not file_bytes:
        return ""

    # 1. UTF-8 with BOM
    if file_bytes.startswith(b"\xef\xbb\xbf"):
        try:
            return file_bytes.decode("utf-8-sig", errors="replace").strip()
        except Exception:
            pass

    # 2. UTF-16 with BOM
    if file_bytes.startswith(b"\xff\xfe") or file_bytes.startswith(b"\xfe\xff"):
        try:
            return file_bytes.decode("utf-16", errors="replace").strip()
        except Exception:
            pass

    # 3. Standard UTF-8
    try:
        text = file_bytes.decode("utf-8")
        if text:
            printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t ")
            if (printable / len(text)) > 0.85:
                return text.strip()
    except UnicodeDecodeError:
        pass

    # 4. Embedded string sequences (UTF-16LE and ASCII strings from binary files)
    strings = []
    # UTF-16LE runs (standard for Word .doc, Excel .xls, Windows data)
    for m in re.finditer(rb'(?:[\x20-\x7E\t\r\n]\x00){4,}', file_bytes):
        s = m.group().decode("utf-16le", errors="ignore").strip()
        if len(s) >= 4:
            strings.append(s)

    # ASCII/printable runs
    for m in re.finditer(rb'[\x20-\x7E\t\r\n]{4,}', file_bytes):
        s = m.group().decode("ascii", errors="ignore").strip()
        if len(s) >= 4 and not any(s in existing for existing in strings):
            strings.append(s)

    if strings:
        # Filter out common binary noise or short fragments
        clean_strings = [s for s in strings if any(c.isalpha() for c in s)]
        return "\n".join(clean_strings[:500]).strip()

    # 5. Latin-1 fallback
    try:
        text = file_bytes.decode("latin-1")
        printable = sum(1 for c in text if c.isprintable() or c in "\r\n\t ")
        if len(text) > 0 and (printable / len(text)) > 0.85:
            return text.strip()
    except Exception:
        pass

    return ""


def extract_file_content(name: str, file_bytes: bytes, mimetype: str = "", filetype: str = "") -> Tuple[Optional[str], str]:
    """
    Unified extractor for ANY file type.
    Returns (extracted_text, file_type_label).
    If text could be extracted, extracted_text is a non-empty string.
    If purely binary with no extractable text, extracted_text is None.
    """
    name_lower = name.lower()
    mimetype_lower = (mimetype or "").lower()
    filetype_lower = (filetype or "").lower()

    # Word Documents (.docx, .doc, .dotx)
    if name_lower.endswith(".docx") or filetype_lower in ["docx", "dotx"] or "wordprocessingml" in mimetype_lower:
        text = extract_text_from_docx(file_bytes)
        if not text:
            text = extract_text_from_doc(file_bytes)
        return (text, "Word Document")

    if name_lower.endswith(".doc") or filetype_lower == "doc" or "msword" in mimetype_lower:
        # Try docx first in case it's actually an OpenXML file named .doc
        text = extract_text_from_docx(file_bytes)
        if not text:
            text = extract_text_from_doc(file_bytes)
        return (text, "Word Document")

    # Excel Spreadsheets (.xlsx, .xlsm, .xls)
    if name_lower.endswith((".xlsx", ".xlsm", ".xltx")) or filetype_lower in ["xlsx", "xlsm", "xltx"] or "spreadsheetml" in mimetype_lower:
        text = extract_text_from_xlsx(file_bytes)
        if not text:
            text = extract_generic_text(file_bytes)
        return (text, "Excel Spreadsheet")

    if name_lower.endswith(".xls") or filetype_lower == "xls" or "ms-excel" in mimetype_lower:
        text = extract_text_from_xlsx(file_bytes)
        if not text:
            text = extract_generic_text(file_bytes)
        return (text, "Excel Spreadsheet")

    # PowerPoint Presentations (.pptx, .ppt)
    if name_lower.endswith((".pptx", ".potx")) or filetype_lower in ["pptx", "potx"] or "presentationml" in mimetype_lower:
        text = extract_text_from_pptx(file_bytes)
        if not text:
            text = extract_generic_text(file_bytes)
        return (text, "PowerPoint Presentation")

    if name_lower.endswith(".ppt") or filetype_lower == "ppt" or "powerpoint" in mimetype_lower:
        text = extract_text_from_pptx(file_bytes)
        if not text:
            text = extract_generic_text(file_bytes)
        return (text, "PowerPoint Presentation")

    # Archives (.zip, .tar, .tar.gz, .tgz, .gz)
    if name_lower.endswith(".zip") or filetype_lower == "zip" or "zip" in mimetype_lower:
        text = extract_text_from_zip(file_bytes)
        return (text, "ZIP Archive")

    if name_lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2")) or filetype_lower in ["tar", "gz", "tgz"]:
        text = extract_text_from_tar(file_bytes)
        return (text, "TAR Archive")

    # Generic text, code, config, scripts
    text = extract_generic_text(file_bytes)
    if text:
        return (text, "Document/File")

    # Pure binary / media without readable text
    return (None, "Binary/Media File")
