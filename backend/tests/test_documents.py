"""Real file/SQLite HTTP tests. No mocked successful uploads or extraction results."""

import asyncio
import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_auth import create_member, headers, login, register

from app.documents.models import DocumentVersion
from app.documents.storage import LocalObjectStore, StorageUnavailable

CONTENT = b"SYNTHETIC COMMERCIAL LEASE\nTenant: Example Ltd\nRent: 5000\n"


@pytest.fixture
def store(client: TestClient, tmp_path: Path) -> LocalObjectStore:
    storage = LocalObjectStore(tmp_path / "objects")
    client.app.state.object_store = storage  # type: ignore[union-attr]
    return storage


def upload(client: TestClient, pair: dict[str, str], content: bytes = CONTENT, **data: str) -> dict:
    response = client.post(
        "/api/v1/documents",
        headers=headers(pair),
        files={"file": ("synthetic.txt", content, "text/plain")},
        data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_upload_metadata_versions_download_archive(
    client: TestClient, store: LocalObjectStore
) -> None:
    pair = register(client)
    doc = upload(
        client,
        pair,
        title="Synthetic lease",
        document_type="LEASE",
        property_name="Demo property",
        notes="Synthetic only",
    )
    assert doc["status"] == "UPLOADED" and doc["classification"] == "UNCLASSIFIED"
    assert doc["latest_version"]["page_count"] is None
    assert doc["latest_version"]["scan_status"] == "NOT_SCANNED"
    assert "object_key" not in doc["latest_version"]
    assert doc["metadata"]["property_name"] == "Demo property"
    prefix = f"/api/v1/documents/{doc['id']}"
    response = client.get(prefix + "/versions/1/download", headers=headers(pair))
    assert response.status_code == 200 and response.content == CONTENT
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    updated = client.patch(
        prefix, headers=headers(pair), json={"title": "Updated title", "notes": None}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["metadata"]["notes"] is None
    assert (
        client.patch(prefix, headers=headers(pair), json={"status": "COMPLETED"}).status_code == 422
    )
    assert client.patch(prefix, headers=headers(pair), json={"title": None}).status_code == 422
    second = client.post(
        prefix + "/versions",
        headers=headers(pair),
        files={"file": ("v2.txt", CONTENT + b"Amendment\n", "text/plain")},
        data={"expected_version": "1"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["current_version"] == 2
    versions = client.get(prefix + "/versions", headers=headers(pair)).json()
    assert [version["version_number"] for version in versions] == [2, 1]
    assert client.get(prefix + "/versions/1/download", headers=headers(pair)).content == CONTENT
    stale = client.post(
        prefix + "/versions",
        headers=headers(pair),
        files={"file": ("v3.txt", b"Other text", "text/plain")},
        data={"expected_version": "1"},
    )
    assert stale.status_code == 409
    duplicate = client.post(
        prefix + "/versions",
        headers=headers(pair),
        files={"file": ("again.txt", CONTENT, "text/plain")},
        data={"expected_version": "2"},
    )
    assert duplicate.status_code == 409
    archived = client.post(prefix + "/archive", headers=headers(pair))
    assert archived.status_code == 200 and archived.json()["status"] == "ARCHIVED"
    assert client.patch(prefix, headers=headers(pair), json={"title": "no"}).status_code == 409
    assert client.get(prefix + "/versions/1/download", headers=headers(pair)).status_code == 200
    assert client.post(prefix + "/restore", headers=headers(pair)).json()["status"] == "UPLOADED"
    assert client.get(prefix + "/versions/99/download", headers=headers(pair)).status_code == 404


def test_tenant_isolation_and_parent_link(client: TestClient, store: LocalObjectStore) -> None:
    alpha, beta = register(client), register(client, "beta")
    doc = upload(client, alpha)
    prefix = f"/api/v1/documents/{doc['id']}"
    for path in [prefix, prefix + "/versions", prefix + "/versions/1/download"]:
        assert (
            client.get(
                path, headers={**headers(beta), "X-Organization-ID": doc["organization_id"]}
            ).status_code
            == 404
        )
    for suffix in ["/archive", "/restore"]:
        assert client.post(prefix + suffix, headers=headers(beta)).status_code == 404
    assert (
        client.patch(prefix, headers=headers(beta), json={"notes": "Cross tenant"}).status_code
        == 404
    )
    assert (
        client.post(
            prefix + "/versions",
            headers=headers(beta),
            files={"file": ("v2.txt", CONTENT, "text/plain")},
            data={"expected_version": "1"},
        ).status_code
        == 404
    )
    response = client.post(
        "/api/v1/documents",
        headers=headers(beta),
        files={"file": ("child.txt", CONTENT, "text/plain")},
        data={"parent_document_id": doc["id"]},
    )
    assert response.status_code == 404
    assert client.get("/api/v1/documents", headers=headers(beta)).json()["total"] == 0
    child = upload(client, alpha, parent_document_id=doc["id"])
    assert child["parent_document_id"] == doc["id"]
    spoofed = upload(client, beta, organization_id=doc["organization_id"])
    assert spoofed["organization_id"] != doc["organization_id"]


@pytest.mark.parametrize(
    "role,can_write",
    [
        ("VIEWER", False),
        ("AUDITOR", False),
        ("REVIEWER", False),
        ("ANALYST", True),
        ("ORG_ADMIN", True),
    ],
)
def test_document_roles(
    client: TestClient, store: LocalObjectStore, role: str, can_write: bool
) -> None:
    admin = register(client)
    doc = upload(client, admin)
    create_member(client, admin, role)
    member = login(client, "member@example.com")
    assert client.get("/api/v1/documents", headers=headers(member)).status_code == 200
    assert (
        client.get(
            f"/api/v1/documents/{doc['id']}/versions/1/download", headers=headers(member)
        ).status_code
        == 200
    )
    response = client.post(
        "/api/v1/documents",
        headers=headers(member),
        files={"file": ("lease.txt", CONTENT, "text/plain")},
    )
    assert response.status_code == (201 if can_write else 403)
    assert client.patch(
        f"/api/v1/documents/{doc['id']}", headers=headers(member), json={"notes": "Changed"}
    ).status_code == (200 if can_write else 403)


@pytest.mark.parametrize(
    "filename,mime,content,status",
    [
        ("lease.exe", "application/octet-stream", b"MZfake", 415),
        ("../lease.txt", "text/plain", CONTENT, 422),
        ("lease.pdf", "text/plain", CONTENT, 415),
        ("lease.pdf", "application/pdf", b"not a PDF", 415),
        ("lease.txt", "text/plain", b"", 422),
        ("lease.txt", "text/plain", b"\xff\xfe", 415),
        ("lease.txt", "text/plain", b"binary\0text", 415),
        (
            "lease.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            b"not a zip",
            415,
        ),
    ],
)
def test_invalid_uploads_do_not_publish(
    client: TestClient,
    store: LocalObjectStore,
    filename: str,
    mime: str,
    content: bytes,
    status: int,
) -> None:
    pair = register(client)
    response = client.post(
        "/api/v1/documents", headers=headers(pair), files={"file": (filename, content, mime)}
    )
    assert response.status_code == status, response.text
    assert client.get("/api/v1/documents", headers=headers(pair)).json()["total"] == 0
    assert not list(store.root.rglob("*"))


def docx_bytes(*, active: bool = False) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body/></w:document>',
        )
        if active:
            archive.writestr("word/vbaProject.bin", b"macro")
    return output.getvalue()


def test_docx_container_and_pdf_signature(client: TestClient, store: LocalObjectStore) -> None:
    pair = register(client)
    mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert (
        client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={"file": ("lease.docx", docx_bytes(), mime)},
        ).status_code
        == 201
    )
    assert (
        client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={"file": ("lease.docx", docx_bytes(active=True), mime)},
        ).status_code
        == 415
    )
    # Signature check only: no claim of full PDF structure parsing or malware scanning.
    pdf = client.post(
        "/api/v1/documents",
        headers=headers(pair),
        files={
            "file": (
                "lease.pdf",
                b"%PDF-1.7\nsynthetic signature fixture\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert pdf.status_code == 201 and pdf.json()["latest_version"]["page_count"] is None


def test_size_limits_and_no_auth(client: TestClient, store: LocalObjectStore) -> None:
    pair = register(client)
    client.app.state.settings.max_upload_bytes = 1024  # type: ignore[union-attr]
    response = client.post(
        "/api/v1/documents",
        headers=headers(pair),
        files={"file": ("large.txt", b"x" * 1025, "text/plain")},
    )
    assert response.status_code == 413
    # Early declared request cap is independent of file read cap.
    response = client.post(
        "/api/v1/documents",
        headers={**headers(pair), "Content-Length": str(200 * 1024 * 1024)},
        content=b"x",
    )
    assert response.status_code == 413
    assert client.get("/api/v1/documents").status_code == 401


def test_compensates_storage_and_database_failures(
    client: TestClient, store: LocalObjectStore
) -> None:
    pair = register(client)
    with patch.object(
        store, "put", new=AsyncMock(side_effect=StorageUnavailable("private-secret"))
    ):
        response = client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={"file": ("lease.txt", CONTENT, "text/plain")},
        )
        assert response.status_code == 503 and "private-secret" not in response.text
    # A publication failure after storing bytes must compensate its unique object key.
    with (
        patch(
            "app.documents.service.describe",
            new=AsyncMock(side_effect=RuntimeError("private-secret")),
        ),
        pytest.raises(RuntimeError),
    ):
        client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={"file": ("lease.txt", CONTENT, "text/plain")},
        )
    assert client.get("/api/v1/documents", headers=headers(pair)).json()["total"] == 0
    assert not [path for path in store.root.rglob("*") if path.is_file()]


def test_integrity_check_and_pagination(client: TestClient, store: LocalObjectStore) -> None:
    pair = register(client)
    doc = upload(client, pair, title="100% lease")
    upload(client, pair, title="Another lease")
    page = client.get("/api/v1/documents?limit=1&offset=1", headers=headers(pair)).json()
    assert page["total"] == 2 and len(page["items"]) == 1
    assert client.get("/api/v1/documents?q=%25", headers=headers(pair)).json()["total"] == 1

    async def corrupt() -> None:
        async with client.app.state.test_factory() as db:  # type: ignore[union-attr]
            from uuid import UUID

            version = await db.scalar(
                select(DocumentVersion).where(DocumentVersion.document_id == UUID(doc["id"]))
            )
            store.path(version.object_key).write_bytes(b"tampered")

    asyncio.run(corrupt())
    response = client.get(
        f"/api/v1/documents/{doc['id']}/versions/1/download", headers=headers(pair)
    )
    assert response.status_code == 503 and b"tampered" not in response.content


def test_rejects_storage_path_traversal(store: LocalObjectStore) -> None:
    with pytest.raises(StorageUnavailable):
        store.path("../../outside")


def test_chunked_body_limit_cannot_be_bypassed(client: TestClient, store: LocalObjectStore) -> None:
    from app.documents.limits import UploadLimitMiddleware

    pair = register(client)
    middleware = client.app.middleware_stack  # type: ignore[union-attr]
    while not isinstance(middleware, UploadLimitMiddleware):
        middleware = middleware.app
    middleware.limit = 2048

    def content():
        yield b'--test-boundary\r\nContent-Disposition: form-data; name="file"; filename="large.txt"\r\nContent-Type: text/plain\r\n\r\n'
        yield b"x" * 4096
        yield b"\r\n--test-boundary--\r\n"

    response = client.post(
        "/api/v1/documents",
        headers={**headers(pair), "Content-Type": "multipart/form-data; boundary=test-boundary"},
        content=content(),
    )
    assert response.status_code == 413, response.text
    assert client.get("/api/v1/documents", headers=headers(pair)).json()["total"] == 0


def test_upload_policy_reports_real_limits_and_permissions(
    client: TestClient, store: LocalObjectStore
) -> None:
    pair = register(client)
    policy = client.get("/api/v1/documents/policy", headers=headers(pair)).json()
    assert policy["can_upload"] is True
    assert policy["max_upload_bytes"] == 20 * 1024 * 1024
    assert policy["parsing_enabled"] is False and policy["malware_scanning_enabled"] is False
    create_member(client, pair)
    viewer = login(client, "member@example.com")
    assert (
        client.get("/api/v1/documents/policy", headers=headers(viewer)).json()["can_upload"]
        is False
    )


def test_uncertain_commit_preserves_original_for_reconciliation(
    client: TestClient, store: LocalObjectStore
) -> None:
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import AsyncSession

    pair = register(client)
    with patch.object(
        AsyncSession,
        "commit",
        new=AsyncMock(side_effect=OperationalError("commit", {}, Exception("private-secret"))),
    ):
        response = client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={"file": ("lease.txt", CONTENT, "text/plain")},
        )
    assert response.status_code == 503 and "private-secret" not in response.text
    assert client.get("/api/v1/documents", headers=headers(pair)).json()["total"] == 0
    # A lost commit acknowledgement might mean the row committed. Never delete bytes on uncertainty.
    assert [path.read_bytes() for path in store.root.rglob("*") if path.is_file()] == [CONTENT]


def test_failed_version_keeps_previous_original_and_counter(
    client: TestClient, store: LocalObjectStore
) -> None:
    pair = register(client)
    doc = upload(client, pair)
    prefix = f"/api/v1/documents/{doc['id']}"
    with patch.object(store, "put", new=AsyncMock(side_effect=StorageUnavailable)):
        response = client.post(
            prefix + "/versions",
            headers=headers(pair),
            files={"file": ("v2.txt", CONTENT + b"New version", "text/plain")},
            data={"expected_version": "1"},
        )
        assert response.status_code == 503
    assert client.get(prefix, headers=headers(pair)).json()["current_version"] == 1
    assert client.get(prefix + "/versions/1/download", headers=headers(pair)).content == CONTENT


def test_docx_xml_entities_and_zip_traversal_are_rejected(
    client: TestClient, store: LocalObjectStore
) -> None:
    pair = register(client)
    for unsafe in ("entity", "traversal"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "[Content_Types].xml",
                '<!DOCTYPE doc [<!ENTITY secret SYSTEM "file:///etc/passwd">]><Types>&secret;</Types>'
                if unsafe == "entity"
                else "<Types/>",
            )
            archive.writestr("word/document.xml", "<document/>")
            if unsafe == "traversal":
                archive.writestr("../../escape", b"unsafe")
        response = client.post(
            "/api/v1/documents",
            headers=headers(pair),
            files={
                "file": (
                    "unsafe.docx",
                    output.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 415
    assert client.get("/api/v1/documents", headers=headers(pair)).json()["total"] == 0
