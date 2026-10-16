"""Pure parser interfaces and bounded normalized output. Content is data, never instructions."""

import io
import re
import unicodedata
import zipfile
from typing import Literal, Protocol

from defusedxml import ElementTree
from pydantic import BaseModel, Field
from pypdf import PdfReader

PARSER_REVISION = "leaseguard-parser-v1"


class ParseFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Heading(BaseModel):
    text: str
    start: int
    end: int


class ParsedPage(BaseModel):
    number: int
    text: str
    headings: list[Heading] = Field(default_factory=list)
    tables: list[list[list[str]]] = Field(default_factory=list)


class ParsedDocument(BaseModel):
    parser_revision: str = PARSER_REVISION
    page_number_kind: Literal["PHYSICAL", "LOGICAL"]
    pages: list[ParsedPage]


class ParseLimits(BaseModel):
    max_pages: int = 500
    max_characters: int = 2_000_000
    chunk_size: int = 2000
    chunk_overlap: int = 200
    max_chunks: int = 3000


class OCRProvider(Protocol):
    def recognize(self, content: bytes, page_number: int) -> str: ...


class DisabledOCRProvider:
    def recognize(self, content: bytes, page_number: int) -> str:
        raise ParseFailure("OCR_REQUIRED")


class DocumentParser(Protocol):
    def parse(self, content: bytes, limits: ParseLimits) -> ParsedDocument: ...


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
    # Preserve layout/newlines, remove invalid control and surrogate characters.
    return "".join(
        char for char in text if char in "\n\t" or unicodedata.category(char) not in {"Cc", "Cs"}
    )


def page(number: int, text: str, tables: list[list[list[str]]] | None = None) -> ParsedPage:
    normalized = normalize(text)
    headings = []
    offset = 0
    for line in normalized.splitlines(keepends=True):
        candidate = line.strip()
        if 2 <= len(candidate) <= 150 and (
            re.match(r"^(?:\d+(?:\.\d+)*[.)]?\s+|(?:SECTION|ARTICLE)\s+)", candidate, re.IGNORECASE)
            or (candidate.isupper() and any(c.isalpha() for c in candidate))
        ):
            start = offset + len(line) - len(line.lstrip())
            headings.append(Heading(text=candidate, start=start, end=start + len(candidate)))
        offset += len(line)
    return ParsedPage(number=number, text=normalized, headings=headings, tables=tables or [])


def enforce(result: ParsedDocument, limits: ParseLimits) -> ParsedDocument:
    if not result.pages or len(result.pages) > limits.max_pages:
        raise ParseFailure("PAGE_LIMIT")
    if sum(len(item.text) for item in result.pages) > limits.max_characters:
        raise ParseFailure("TEXT_LIMIT")
    if not any(item.text.strip() for item in result.pages):
        raise ParseFailure("NO_TEXT")
    return result


class TextParser:
    def parse(self, content: bytes, limits: ParseLimits) -> ParsedDocument:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ParseFailure("INVALID_TEXT") from exc
        # Form feeds delimit explicit logical segments, not physical printed pages.
        return enforce(
            ParsedDocument(
                page_number_kind="LOGICAL",
                pages=[page(i + 1, value) for i, value in enumerate(text.split("\f"))],
            ),
            limits,
        )


class PDFParser:
    def __init__(self, ocr: OCRProvider | None = None):
        self.ocr = ocr or DisabledOCRProvider()

    def parse(self, content: bytes, limits: ParseLimits) -> ParsedDocument:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise ParseFailure("ENCRYPTED_PDF")
        if len(reader.pages) > limits.max_pages:
            raise ParseFailure("PAGE_LIMIT")
        pages = []
        characters = 0
        for index, pdf_page in enumerate(reader.pages):
            text = pdf_page.extract_text() or ""
            if len(text.strip()) < 20 and len(pdf_page.images) > 0:
                text = self.ocr.recognize(content, index + 1)
            characters += len(text)
            if characters > limits.max_characters:
                raise ParseFailure("TEXT_LIMIT")
            pages.append(page(index + 1, text))
        return enforce(ParsedDocument(page_number_kind="PHYSICAL", pages=pages), limits)


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class DOCXParser:
    def parse(self, content: bytes, limits: ParseLimits) -> ParsedDocument:
        # Reuse intake checks: safe ZIP paths, expansion bounds, no obvious active/embedded content.
        from app.documents.validation import validate_docx

        validate_docx(content)
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        body = root.find(f"{W}body")
        if body is None:
            raise ParseFailure("INVALID_DOCX")
        segments: list[ParsedPage] = []
        lines: list[str] = []
        tables: list[list[list[str]]] = []
        total = 0
        for element in body:
            if element.tag == f"{W}p":
                text = "".join(node.text or "" for node in element.iter(f"{W}t"))
                lines.append(text)
                total += len(text) + 1
                if any(node.attrib.get(f"{W}type") == "page" for node in element.iter(f"{W}br")):
                    segments.append(page(len(segments) + 1, "\n".join(lines), tables))
                    lines, tables = [], []
            elif element.tag == f"{W}tbl":
                rows = [
                    [
                        "".join(node.text or "" for node in cell.iter(f"{W}t"))
                        for cell in row.findall(f"{W}tc")
                    ]
                    for row in element.findall(f"{W}tr")
                ]
                tables.append(rows)
                text = "\n".join("\t".join(row) for row in rows)
                lines.append(text)
                total += len(text) + 1
            if total > limits.max_characters:
                raise ParseFailure("TEXT_LIMIT")
            if len(segments) > limits.max_pages:
                raise ParseFailure("PAGE_LIMIT")
        if lines or not segments:
            segments.append(page(len(segments) + 1, "\n".join(lines), tables))
        return enforce(ParsedDocument(page_number_kind="LOGICAL", pages=segments), limits)


def parse_document(content: bytes, mime_type: str, limits: ParseLimits) -> ParsedDocument:
    parsers: dict[str, DocumentParser] = {
        "text/plain": TextParser(),
        "application/pdf": PDFParser(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DOCXParser(),
    }
    parser = parsers.get(mime_type)
    if parser is None:
        raise ParseFailure("UNSUPPORTED_TYPE")
    return parser.parse(content, limits)


class Chunk(BaseModel):
    index: int
    page_number: int
    start: int
    end: int
    section: str | None
    text: str


def chunk_document(document: ParsedDocument, limits: ParseLimits) -> list[Chunk]:
    if not 0 <= limits.chunk_overlap < limits.chunk_size:
        raise ParseFailure("INVALID_CHUNK_POLICY")
    chunks: list[Chunk] = []
    for item in document.pages:
        start = 0
        while start < len(item.text):
            end = min(len(item.text), start + limits.chunk_size)
            if end < len(item.text):
                boundary = item.text.rfind("\n", start + limits.chunk_size * 3 // 4, end)
                if boundary != -1:
                    end = boundary + 1
            if item.text[start:end].strip():
                section = next(
                    (heading.text for heading in reversed(item.headings) if heading.start <= start),
                    None,
                )
                chunks.append(
                    Chunk(
                        index=len(chunks),
                        page_number=item.number,
                        start=start,
                        end=end,
                        section=section,
                        text=item.text[start:end],
                    )
                )
                if len(chunks) > limits.max_chunks:
                    raise ParseFailure("CHUNK_LIMIT")
            if end == len(item.text):
                break
            start = max(start + 1, end - limits.chunk_overlap)
    if not chunks:
        raise ParseFailure("NO_TEXT")
    return chunks
