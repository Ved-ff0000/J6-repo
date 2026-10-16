import asyncio
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.auth.dependencies import Principal, Session, require_permission
from app.core.errors import ErrorEnvelope
from app.documents.models import Document, DocumentStatus
from app.documents.service import get_document
from app.graph.models import GraphSnapshot
from app.graph.schemas import Assertions, CreateSnapshot, Edge, Node
from app.graph.service import GraphFailure, evidence, latest, verify
from app.graph.store import Projection, Scope
from app.processing.router import scoped_version

router = APIRouter(
    prefix="/api/v1",
    tags=["knowledge-graph"],
    responses={code: {"model": ErrorEnvelope} for code in (401, 403, 404, 409, 503)},
)
Readers = Annotated[Principal, Depends(require_permission("documents:read"))]
Writers = Annotated[Principal, Depends(require_permission("documents:write"))]


class SnapshotOut(BaseModel):
    id: UUID
    revision: int
    status: str
    assertion_status: Literal["HUMAN_ASSERTED_UNVERIFIED"] = "HUMAN_ASSERTED_UNVERIFIED"
    created_by: UUID
    created_at: datetime
    attempts: int
    error_code: str | None
    assertions: Assertions


class GraphOut(BaseModel):
    snapshot_id: UUID
    revision: int
    assertion_status: Literal["HUMAN_ASSERTED_UNVERIFIED"] = "HUMAN_ASSERTED_UNVERIFIED"
    nodes: list[Node]
    edges: list[Edge]
    traversal: Literal["FULL_SCOPE", "UNDIRECTED_NEIGHBORHOOD"]


def out(snapshot: GraphSnapshot) -> SnapshotOut:
    try:
        assertions = Assertions.model_validate(snapshot.assertions)
    except ValueError as exc:
        raise HTTPException(409, "GRAPH_INTEGRITY_ERROR") from exc
    if assertions.digest() != snapshot.content_hash:
        raise HTTPException(409, "GRAPH_INTEGRITY_ERROR")
    return SnapshotOut(
        id=snapshot.id,
        revision=snapshot.revision,
        status=snapshot.status,
        created_by=snapshot.created_by,
        created_at=snapshot.created_at,
        attempts=snapshot.attempts,
        error_code=snapshot.error_code,
        assertions=assertions,
    )


def enabled(request: Request) -> None:
    if not request.app.state.settings.graph_enabled:
        raise HTTPException(503, "Knowledge graph is disabled")


async def editable(
    db: Session, principal: Writers, request: Request, document_id: UUID, version_number: int
) -> UUID:
    document = await get_document(db, principal.organization_id, document_id, lock=True)
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    enabled(request)
    if (
        document.status == DocumentStatus.ARCHIVED
        or document.current_version != version_number
        or version.processing_status != "PARSED"
    ):
        raise HTTPException(
            409, "Only the latest parsed, non-archived version accepts graph changes"
        )
    if version.scan_status != "CLEAN" and not request.app.state.settings.allow_unscanned_processing:
        raise HTTPException(409, "Unscanned graph projection is disabled")
    return version.id


@router.get("/graph/config")
async def configuration(request: Request, principal: Readers) -> dict[str, object]:
    from app.graph.schemas import ENDPOINTS, KINDS

    return {
        "enabled": request.app.state.settings.graph_enabled,
        "assertion_status": "HUMAN_ASSERTED_UNVERIFIED",
        "node_kinds": KINDS,
        "relationships": ENDPOINTS,
        "max_nodes": 100,
        "max_edges": 200,
    }


@router.get(
    "/documents/{document_id}/versions/{version_number}/graph/snapshot",
    response_model=SnapshotOut | None,
)
async def current_snapshot(
    document_id: UUID, version_number: int, principal: Readers, db: Session
) -> SnapshotOut | None:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    snapshot = await latest(db, principal.organization_id, version.id)
    return out(snapshot) if snapshot else None


@router.get(
    "/documents/{document_id}/versions/{version_number}/graph/snapshots",
    response_model=list[SnapshotOut],
)
async def history(
    document_id: UUID,
    version_number: int,
    principal: Readers,
    db: Session,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[SnapshotOut]:
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    rows = await db.scalars(
        select(GraphSnapshot)
        .where(
            GraphSnapshot.organization_id == principal.organization_id,
            GraphSnapshot.version_id == version.id,
        )
        .order_by(GraphSnapshot.revision.desc())
        .limit(limit)
        .offset(offset)
    )
    return [out(row) for row in rows]


@router.post(
    "/documents/{document_id}/versions/{version_number}/graph/snapshots",
    response_model=SnapshotOut,
    status_code=202,
)
async def create(
    document_id: UUID,
    version_number: int,
    body: CreateSnapshot,
    request: Request,
    principal: Writers,
    db: Session,
) -> SnapshotOut:
    version_id = await editable(db, principal, request, document_id, version_number)
    current = await latest(db, principal.organization_id, version_id)
    revision = current.revision if current else 0
    if revision != body.expected_revision:
        raise HTTPException(409, "Graph revision changed; reload before submitting")
    assertions = Assertions.model_validate(body.model_dump(exclude={"expected_revision"}))
    try:
        hashes = await evidence(db, principal.organization_id, document_id, version_id, assertions)
    except GraphFailure as exc:
        raise HTTPException(
            404
            if exc.code == "EVIDENCE_NOT_FOUND"
            else 422
            if exc.code == "NAME_NOT_IN_EVIDENCE"
            else 409,
            exc.code,
        ) from exc
    snapshot = GraphSnapshot(
        organization_id=principal.organization_id,
        document_id=document_id,
        version_id=version_id,
        revision=revision + 1,
        created_by=principal.user.id,
        requested_by=principal.user.id,
        assertions=assertions.model_dump(mode="json"),
        evidence_hashes=hashes,
        content_hash=assertions.digest(),
    )
    db.add(snapshot)
    await db.flush()
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "GRAPH_REQUESTED",
        "graph_snapshot",
        snapshot.id,
        principal.user.id,
    )
    result = out(snapshot)
    await db.commit()
    return result


@router.post(
    "/documents/{document_id}/versions/{version_number}/graph/snapshots/{revision}/project",
    response_model=SnapshotOut,
    status_code=202,
)
async def project(
    document_id: UUID,
    version_number: int,
    revision: int,
    request: Request,
    principal: Writers,
    db: Session,
    rebuild: bool = False,
) -> SnapshotOut:
    version_id = await editable(db, principal, request, document_id, version_number)
    snapshot = await latest(db, principal.organization_id, version_id)
    if snapshot is None or snapshot.revision != revision:
        raise HTTPException(409, "Only the latest graph revision can be projected")
    if snapshot.status in ("QUEUED", "RUNNING") or (snapshot.status == "READY" and not rebuild):
        return out(snapshot)
    if snapshot.status == "READY":
        snapshot.attempts = 0
    if snapshot.attempts >= request.app.state.settings.graph_max_attempts:
        raise HTTPException(409, "Projection retry limit reached")
    try:
        await verify(db, snapshot)
    except GraphFailure as exc:
        raise HTTPException(409, exc.code) from exc
    snapshot.status = "QUEUED"
    snapshot.generation = None
    snapshot.error_code = None
    snapshot.finished_at = None
    snapshot.lease_expires_at = None
    snapshot.requested_by = principal.user.id
    await db.flush()
    from app.governance.service import audit

    await audit(
        db,
        principal.organization_id,
        "GRAPH_REQUESTED",
        "graph_snapshot",
        snapshot.id,
        principal.user.id,
    )
    result = out(snapshot)
    await db.commit()
    return result


def check_projection(projection: Projection, assertions: Assertions) -> None:
    nodes = {node.id: node.model_dump(mode="json") for node in projection.nodes}
    edges = {edge.id: edge.model_dump(mode="json") for edge in projection.edges}
    if (
        projection.content_hash != assertions.digest()
        or len(nodes) != len(projection.nodes)
        or len(edges) != len(projection.edges)
        or nodes != {node.id: node.model_dump(mode="json") for node in assertions.nodes}
        or edges != {edge.id: edge.model_dump(mode="json") for edge in assertions.edges}
    ):
        raise HTTPException(409, "GRAPH_INTEGRITY_ERROR")


@router.get("/documents/{document_id}/versions/{version_number}/graph", response_model=GraphOut)
async def graph(
    document_id: UUID,
    version_number: int,
    request: Request,
    principal: Readers,
    db: Session,
    revision: Annotated[int | None, Query(ge=1)] = None,
    root_id: Annotated[str | None, Query(min_length=1, max_length=40)] = None,
    hops: Annotated[int, Query(ge=1, le=3)] = 1,
) -> GraphOut:
    document = await get_document(db, principal.organization_id, document_id)
    if document.status == DocumentStatus.ARCHIVED:
        raise HTTPException(409, "Archived graph scopes are not active")
    version = await scoped_version(db, principal.organization_id, document_id, version_number)
    enabled(request)
    snapshot = (
        await latest(db, principal.organization_id, version.id)
        if revision is None
        else await db.scalar(
            select(GraphSnapshot).where(
                GraphSnapshot.organization_id == principal.organization_id,
                GraphSnapshot.version_id == version.id,
                GraphSnapshot.revision == revision,
            )
        )
    )
    if snapshot is None:
        raise HTTPException(404, "Graph snapshot not found")
    if snapshot.status != "READY" or snapshot.generation is None:
        raise HTTPException(409, "Graph projection is not ready")
    try:
        assertions = await verify(db, snapshot)
        async with asyncio.timeout(20):
            projection = await request.app.state.graph_store.read(
                Scope(
                    principal.organization_id,
                    document_id,
                    version.id,
                    snapshot.id,
                    snapshot.generation,
                )
            )
        check_projection(projection, assertions)
        # SQL eligibility must still hold after external I/O.
        fresh = (
            await db.execute(
                select(GraphSnapshot.status, GraphSnapshot.generation, Document.status)
                .join(Document, Document.id == GraphSnapshot.document_id)
                .where(
                    GraphSnapshot.id == snapshot.id,
                    GraphSnapshot.organization_id == principal.organization_id,
                    Document.organization_id == principal.organization_id,
                )
            )
        ).one_or_none()
        current = await latest(db, principal.organization_id, version.id)
        if (
            fresh is None
            or fresh[0] != "READY"
            or fresh[1] != snapshot.generation
            or fresh[2] == DocumentStatus.ARCHIVED
            or (revision is None and (current is None or current.id != snapshot.id))
        ):
            raise HTTPException(409, "Graph scope changed; reload")
        await verify(db, snapshot)
    except GraphFailure as exc:
        raise HTTPException(
            503 if exc.code in {"GRAPH_UNAVAILABLE", "GRAPH_DISABLED", "GRAPH_NOT_FOUND"} else 409,
            exc.code,
        ) from exc
    except TimeoutError as exc:
        raise HTTPException(503, "GRAPH_TIMEOUT") from exc
    nodes = assertions.nodes
    edges = assertions.edges
    if root_id is not None:
        if root_id not in {node.id for node in nodes}:
            raise HTTPException(404, "Graph node not found")
        selected = {root_id}
        for _ in range(hops):
            selected |= {
                endpoint
                for edge in edges
                if edge.source in selected or edge.target in selected
                for endpoint in (edge.source, edge.target)
            }
        nodes = [node for node in nodes if node.id in selected]
        edges = [edge for edge in edges if edge.source in selected and edge.target in selected]
    return GraphOut(
        snapshot_id=snapshot.id,
        revision=snapshot.revision,
        nodes=nodes,
        edges=edges,
        traversal="UNDIRECTED_NEIGHBORHOOD" if root_id is not None else "FULL_SCOPE",
    )
