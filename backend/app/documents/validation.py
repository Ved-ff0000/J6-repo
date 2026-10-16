import hashlib
import io
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import PurePath
from zlib import error as ZlibError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from fastapi import HTTPException, UploadFile

TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class ValidatedUpload:
    filename: str
    mime_type: str
    content: bytes
    checksum: str


def invalid(message: str) -> HTTPException:
    return HTTPException(415, message)


def validate_docx(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries]
            if len(entries) > 1000 or len(names) != len(set(names)):
                raise invalid("Unsafe DOCX archive")
            if sum(entry.file_size for entry in entries) > 100 * 1024 * 1024:
                raise invalid("DOCX expanded content is too large")
            for entry in entries:
                path = entry.filename.replace("\\", "/")
                if path.startswith("/") or ".." in path.split("/") or entry.flag_bits & 1:
                    raise invalid("Unsafe DOCX archive")
                if (
                    entry.file_size > 20 * 1024 * 1024
                    or entry.file_size > max(1, entry.compress_size) * 200
                ):
                    raise invalid("Unsafe DOCX compression ratio")
                if any(
                    term in path.casefold() for term in ("vbaproject", "activex", "embeddings/")
                ):
                    raise invalid("Active or embedded DOCX content is not accepted")
            if not {"[Content_Types].xml", "word/document.xml"}.issubset(names):
                raise invalid("File is not a DOCX document")
            root = ElementTree.fromstring(archive.read("[Content_Types].xml"))
            if not any(
                node.attrib.get("PartName") == "/word/document.xml"
                and node.attrib.get("ContentType")
                == "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
                for node in root
            ):
                raise invalid("File is not a macro-free DOCX document")
            document = ElementTree.fromstring(archive.read("word/document.xml"))
            if (
                document.tag
                != "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}document"
            ):
                raise invalid("Invalid DOCX document XML")
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        NotImplementedError,
        EOFError,
        ValueError,
        ZlibError,
        KeyError,
        RuntimeError,
        DefusedXmlException,
        ElementTree.ParseError,
    ) as exc:
        raise invalid("Invalid or unsafe DOCX document") from exc


def validate_content(filename: str, mime_type: str | None, content: bytes) -> ValidatedUpload:
    name = unicodedata.normalize("NFKC", filename).strip()
    if (
        not name
        or len(name) > 255
        or any(unicodedata.category(char).startswith("C") for char in name)
        or "/" in name
        or "\\" in name
        or name.startswith(".")
    ):
        raise HTTPException(422, "Filename must be a safe name without paths or control characters")
    suffix = PurePath(name).suffix.casefold()
    if suffix not in TYPES:
        raise invalid("Only PDF, DOCX and UTF-8 TXT files are accepted")
    expected = TYPES[suffix]
    declared = (mime_type or "application/octet-stream").split(";")[0].strip().casefold()
    if declared not in {expected, "application/octet-stream"}:
        raise invalid("Declared MIME type does not match the filename")
    if not content:
        raise HTTPException(422, "Empty documents are not accepted")
    if suffix == ".pdf" and not (content.startswith(b"%PDF-") and b"%%EOF" in content[-2048:]):
        raise invalid("File does not have a supported PDF signature")
    if suffix == ".docx":
        validate_docx(content)
    if suffix == ".txt":
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise invalid("TXT documents must use UTF-8") from exc
        if (
            not text.strip()
            or any(ord(char) < 32 and char not in "\t\r\n" for char in text)
            or content.startswith((b"%PDF-", b"PK\x03\x04"))
        ):
            raise invalid("TXT document contains empty or binary content")
    return ValidatedUpload(name, expected, content, hashlib.sha256(content).hexdigest())


async def read_upload(file: UploadFile, limit: int) -> ValidatedUpload:
    content = bytearray()
    try:
        while block := await file.read(64 * 1024):
            content.extend(block)
            if len(content) > limit:
                raise HTTPException(413, "Document exceeds the configured upload limit")
        # Container checks/UTF-8 decoding run in a worker, not the async event loop.
        import asyncio

        return await asyncio.to_thread(
            validate_content, file.filename or "", file.content_type, bytes(content)
        )
    finally:
        await file.close()
