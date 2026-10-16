from fastapi.testclient import TestClient

from app.main import app


def test_process_health() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "leaseguard-api", "version": "0.1.0"}


def test_openapi_available() -> None:
    with TestClient(app) as client:
        assert "/api/health" in client.get("/openapi.json").json()["paths"]


def test_unimplemented_workflows_not_exposed() -> None:
    with TestClient(app) as client:
        assert client.get("/api/documents").status_code == 404
