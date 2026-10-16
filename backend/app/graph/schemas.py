"""Manual source-backed assertions, never verified extraction or automatic reasoning."""

import hashlib
import json
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Key = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,39}$")]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Kind = Literal[
    "Tenant", "Landlord", "Lease", "Property", "Clause", "Amendment", "RenewalOption", "Obligation"
]
Relation = Literal[
    "HAS_LEASE",
    "LANDLORD_OF",
    "OWNS",
    "LOCATED_AT",
    "HAS_CLAUSE",
    "HAS_AMENDMENT",
    "HAS_RENEWAL_OPTION",
    "HAS_OBLIGATION",
    "APPLIES_TO",
]
ENDPOINTS = {
    "HAS_LEASE": ("Tenant", "Lease"),
    "LANDLORD_OF": ("Landlord", "Lease"),
    "OWNS": ("Landlord", "Property"),
    "LOCATED_AT": ("Lease", "Property"),
    "HAS_CLAUSE": ("Lease", "Clause"),
    "HAS_AMENDMENT": ("Lease", "Amendment"),
    "HAS_RENEWAL_OPTION": ("Lease", "RenewalOption"),
    "HAS_OBLIGATION": ("Lease", "Obligation"),
    "APPLIES_TO": ("Obligation", "Tenant"),
}
KINDS = (
    "Tenant",
    "Landlord",
    "Lease",
    "Property",
    "Clause",
    "Amendment",
    "RenewalOption",
    "Obligation",
)
Evidence = Annotated[list[UUID], Field(min_length=1, max_length=5)]


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Key
    kind: Kind
    name: Name
    evidence_chunk_ids: Evidence


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Key
    kind: Relation
    source: Key
    target: Key
    evidence_chunk_ids: Evidence


class Assertions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodes: list[Node] = Field(min_length=1, max_length=100)
    edges: list[Edge] = Field(max_length=200)
    rationale: str = Field(min_length=10, max_length=1000)

    @model_validator(mode="after")
    def valid_graph(self) -> "Assertions":
        if len(self.rationale.strip()) < 10:
            raise ValueError("A meaningful rationale is required")
        nodes = {n.id: n for n in self.nodes}
        if len(nodes) != len(self.nodes) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("Graph IDs must be unique within their kind")
        if not any(n.kind == "Lease" for n in self.nodes):
            raise ValueError("Include a source-backed Lease node")
        relationships = set()
        for e in self.edges:
            if (
                e.source not in nodes
                or e.target not in nodes
                or (nodes[e.source].kind, nodes[e.target].kind) != ENDPOINTS[e.kind]
            ):
                raise ValueError("Relationship endpoints do not match allowed domain types")
            key = (e.kind, e.source, e.target)
            if key in relationships:
                raise ValueError("Duplicate relationship")
            relationships.add(key)
        items: list[Node | Edge] = [*self.nodes, *self.edges]
        for item in items:
            if len(set(item.evidence_chunk_ids)) != len(item.evidence_chunk_ids):
                raise ValueError("Duplicate evidence reference")
        return self

    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class CreateSnapshot(Assertions):
    expected_revision: int = Field(ge=0, strict=True)
