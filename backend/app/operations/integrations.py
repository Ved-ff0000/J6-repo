"""Explicit simulation adapters; these never represent external enterprise synchronization."""

from typing import Protocol

from app.rules.engine import fingerprint


class IntegrationProvider(Protocol):
    def sync(self, record: dict[str, object]) -> dict[str, object]: ...


class MockProvider:
    def __init__(self, name: str):
        self.name = name

    def sync(self, record: dict[str, object]) -> dict[str, object]:
        return {
            "mode": "MOCK",
            "provider": self.name,
            "external_request_made": False,
            "record_sha256": fingerprint(record),
            "warning": "Simulation only; no Salesforce, SAP or ServiceNow records changed.",
        }


PROVIDERS: dict[str, IntegrationProvider] = {
    name: MockProvider(name) for name in ("salesforce_mock", "sap_mock", "servicenow_mock")
}
