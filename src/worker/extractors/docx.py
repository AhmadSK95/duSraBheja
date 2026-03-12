"""DOCX text extraction using the document XML payload."""

from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
import zipfile

WORD_NAMESPACE = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}


async def extract_docx(file_path: str) -> str:
    """Extract human-readable text from a DOCX file."""

    def _extract(path: str) -> str:
        with zipfile.ZipFile(path) as archive:
            document_xml = archive.read("word/document.xml")

        root = ET.fromstring(document_xml)
        paragraphs: list[str] = []
        for paragraph in root.findall(".//w:p", WORD_NAMESPACE):
            fragments = [
                node.text.strip()
                for node in paragraph.findall(".//w:t", WORD_NAMESPACE)
                if node.text and node.text.strip()
            ]
            if fragments:
                paragraphs.append("".join(fragments))

        return "\n\n".join(paragraphs).strip()

    return await asyncio.get_event_loop().run_in_executor(None, _extract, file_path)
