import hashlib
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.models import GraphSnapshot
from app.graph.schemas import Assertions, Edge, Node
from app.processing.models import DocumentChunk, DocumentPage


class GraphFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def latest(db: AsyncSession, organization_id: UUID, version_id: UUID) -> GraphSnapshot | None:
    return await db.scalar(
        select(GraphSnapshot)
        .where(
            GraphSnapshot.organization_id == organization_id, GraphSnapshot.version_id == version_id
        )
        .order_by(GraphSnapshot.revision.desc())
        .limit(1)
    )


async def evidence(
    db: AsyncSession,
    organization_id: UUID,
    document_id: UUID,
    version_id: UUID,
    assertions: Assertions,
) -> dict[str, str]:
    items: list[Node | Edge] = [*assertions.nodes, *assertions.edges]
    ids = {identifier for item in items for identifier in item.evidence_chunk_ids}
    rows = (
        await db.execute(
            select(DocumentChunk, DocumentPage)
            .join(
                DocumentPage,
                and_(
                    DocumentPage.version_id == DocumentChunk.version_id,
                    DocumentPage.organization_id == DocumentChunk.organization_id,
                    DocumentPage.page_number == DocumentChunk.page_number,
                ),
            )
            .where(
                DocumentChunk.id.in_(ids),
                DocumentChunk.organization_id == organization_id,
                DocumentChunk.document_id == document_id,
                DocumentChunk.version_id == version_id,
            )
            .execution_options(populate_existing=True)
        )
    ).all()
    if len(rows) != len(ids):
        raise GraphFailure("EVIDENCE_NOT_FOUND")
    chunks = {chunk.id: chunk for chunk, _ in rows}
    for chunk, page in rows:
        if (
            chunk.parser_revision != page.parser_revision
            or chunk.start_offset < 0
            or chunk.end_offset > len(page.text)
            or page.text[chunk.start_offset : chunk.end_offset] != chunk.text
        ):
            raise GraphFailure("SOURCE_MISMATCH")
    for node in assertions.nodes:
        if not any(node.name in chunks[identifier].text for identifier in node.evidence_chunk_ids):
            raise GraphFailure("NAME_NOT_IN_EVIDENCE")
    return {str(chunk.id): hashlib.sha256(chunk.text.encode()).hexdigest() for chunk, _ in rows}


async def verify(db: AsyncSession, snapshot: GraphSnapshot) -> Assertions:
    try:
        assertions = Assertions.model_validate(snapshot.assertions)
    except ValueError as exc:
        raise GraphFailure("GRAPH_INTEGRITY_ERROR") from exc
    if assertions.digest() != snapshot.content_hash:
        raise GraphFailure("GRAPH_INTEGRITY_ERROR")
    hashes = await evidence(
        db, snapshot.organization_id, snapshot.document_id, snapshot.version_id, assertions
    )
    if hashes != snapshot.evidence_hashes:
        raise GraphFailure("SOURCE_MISMATCH")
    return assertions
