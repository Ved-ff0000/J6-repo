"""Parameterized, finite Neo4j projections; never accepts arbitrary Cypher from callers."""

import hashlib
from dataclasses import dataclass
from uuid import UUID

from neo4j import AsyncDriver, AsyncGraphDatabase, Query
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.graph.schemas import ENDPOINTS, KINDS, Assertions, Edge, Node
from app.graph.service import GraphFailure


@dataclass(frozen=True)
class Scope:
    organization_id: UUID
    document_id: UUID
    version_id: UUID
    snapshot_id: UUID
    generation: UUID

    def params(self) -> dict[str, str]:
        result = {key: str(value) for key, value in vars(self).items()}
        result["scope_key"] = hashlib.sha256(":".join(result.values()).encode()).hexdigest()
        return result


class Projection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_hash: str
    nodes: list[Node]
    edges: list[Edge]


class GraphStore:
    def __init__(self, settings: Settings, driver: AsyncDriver | None = None):
        self.database = settings.neo4j_database
        self.driver = driver
        if self.driver is None and settings.graph_enabled:
            assert settings.neo4j_password is not None
            self.driver = AsyncGraphDatabase.driver(
                settings.neo4j_uri,
                auth=(settings.neo4j_username, settings.neo4j_password.get_secret_value()),
                connection_timeout=5,
                connection_acquisition_timeout=5,
                max_transaction_retry_time=5,
                max_connection_pool_size=10,
            )

    async def close(self) -> None:
        if self.driver is not None:
            await self.driver.close()

    async def healthy(self) -> bool:
        try:
            if self.driver is None:
                return False
            async with self.driver.session(database=self.database) as session:
                result = await session.run(Query("RETURN 1 AS ok", timeout=5))
                row = await result.single(strict=True)
                return bool(row and row["ok"] == 1)
        except Exception:  # noqa: BLE001 - readiness exposes no service errors
            return False

    async def provision(self) -> None:
        try:
            if self.driver is None:
                raise GraphFailure("GRAPH_DISABLED")
            async with self.driver.session(database=self.database) as session:
                for query in (
                    "CREATE CONSTRAINT leaseguard_entity_key IF NOT EXISTS FOR (n:LeaseGuardEntity) REQUIRE n.key IS UNIQUE",
                    "CREATE CONSTRAINT leaseguard_projection_key IF NOT EXISTS FOR (n:LeaseGuardProjection) REQUIRE n.scope_key IS UNIQUE",
                    "CREATE INDEX leaseguard_entity_scope IF NOT EXISTS FOR (n:LeaseGuardEntity) ON (n.organization_id,n.version_id,n.generation)",
                ):
                    await (await session.run(Query(query, timeout=15))).consume()
        except Exception as exc:
            raise GraphFailure("GRAPH_UNAVAILABLE") from exc

    async def project(self, scope: Scope, assertions: Assertions) -> None:
        try:
            if self.driver is None:
                raise GraphFailure("GRAPH_DISABLED")
            params = scope.params()
            nodes = [
                {
                    **node.model_dump(mode="json"),
                    "key": hashlib.sha256(
                        (params["scope_key"] + ":" + node.id).encode()
                    ).hexdigest(),
                }
                for node in assertions.nodes
            ]
            keys = {n["id"]: n["key"] for n in nodes}
            async with (
                self.driver.session(database=self.database) as session,
                await session.begin_transaction(timeout=30) as tx,
            ):
                for kind in KINDS:
                    batch = [n for n in nodes if n["kind"] == kind]
                    if not batch:
                        continue
                    # Labels/types come ONLY from server-owned enums, never user text.
                    query = f"""UNWIND $rows AS row
                    MERGE (n:LeaseGuardEntity:{kind} {{key:row.key}})
                    SET n += $scope,n.local_id=row.id,n.kind=row.kind,n.name=row.name,
                        n.evidence_chunk_ids=row.evidence_chunk_ids,n.assertion_status='HUMAN_ASSERTED_UNVERIFIED'
                    RETURN count(n) AS count"""
                    row = await (await tx.run(query, rows=batch, scope=params)).single(strict=True)
                    if row is None or row["count"] != len(batch):
                        raise GraphFailure("GRAPH_INTEGRITY_ERROR")
                for kind in ENDPOINTS:
                    batch = [
                        {
                            **e.model_dump(mode="json"),
                            "source_key": keys[e.source],
                            "target_key": keys[e.target],
                        }
                        for e in assertions.edges
                        if e.kind == kind
                    ]
                    if not batch:
                        continue
                    query = f"""UNWIND $rows AS row
                    MATCH (a:LeaseGuardEntity {{key:row.source_key,organization_id:$organization_id,scope_key:$scope_key}}),
                          (b:LeaseGuardEntity {{key:row.target_key,organization_id:$organization_id,scope_key:$scope_key}})
                    MERGE (a)-[r:{kind} {{local_id:row.id,scope_key:$scope_key}}]->(b)
                    SET r += $scope,r.evidence_chunk_ids=row.evidence_chunk_ids,
                        r.assertion_status='HUMAN_ASSERTED_UNVERIFIED'
                    RETURN count(r) AS count"""
                    row = await (
                        await tx.run(
                            query,
                            rows=batch,
                            scope=params,
                            organization_id=params["organization_id"],
                            scope_key=params["scope_key"],
                        )
                    ).single(strict=True)
                    if row is None or row["count"] != len(batch):
                        raise GraphFailure("GRAPH_INTEGRITY_ERROR")
                await (
                    await tx.run(
                        """MERGE (p:LeaseGuardProjection {scope_key:$scope_key})
                    SET p += $scope,p.content_hash=$content_hash,p.assertion_status='HUMAN_ASSERTED_UNVERIFIED'""",
                        scope_key=params["scope_key"],
                        scope=params,
                        content_hash=assertions.digest(),
                    )
                ).consume()
                await tx.commit()
        except GraphFailure:
            raise
        except Exception as exc:
            raise GraphFailure("GRAPH_UNAVAILABLE") from exc

    async def read(self, scope: Scope) -> Projection:
        try:
            if self.driver is None:
                raise GraphFailure("GRAPH_DISABLED")
            params = scope.params()
            async with (
                self.driver.session(database=self.database) as session,
                await session.begin_transaction(timeout=15) as tx,
            ):
                projection = await (
                    await tx.run(
                        """MATCH (p:LeaseGuardProjection {scope_key:$scope_key,organization_id:$organization_id,document_id:$document_id,version_id:$version_id,snapshot_id:$snapshot_id,generation:$generation}) RETURN p.content_hash AS hash""",
                        parameters=params,
                    )
                ).single(strict=True)
                if projection is None:
                    raise GraphFailure("GRAPH_NOT_FOUND")
                result = await tx.run(
                    """MATCH (n:LeaseGuardEntity {scope_key:$scope_key,organization_id:$organization_id,document_id:$document_id,version_id:$version_id,snapshot_id:$snapshot_id,generation:$generation})
                    WHERE n.kind IN labels(n) AND n.assertion_status='HUMAN_ASSERTED_UNVERIFIED'
                    RETURN n.local_id AS id,n.kind AS kind,n.name AS name,n.evidence_chunk_ids AS evidence_chunk_ids LIMIT 101""",
                    parameters=params,
                )
                nodes = [Node.model_validate(record.data()) async for record in result]
                result = await tx.run(
                    """MATCH (a:LeaseGuardEntity {scope_key:$scope_key,organization_id:$organization_id,document_id:$document_id,version_id:$version_id,snapshot_id:$snapshot_id,generation:$generation})-[r]->(b:LeaseGuardEntity {scope_key:$scope_key,organization_id:$organization_id,document_id:$document_id,version_id:$version_id,snapshot_id:$snapshot_id,generation:$generation})
                    WHERE r.scope_key=$scope_key AND r.organization_id=$organization_id AND r.document_id=$document_id AND r.version_id=$version_id
                      AND r.generation=$generation AND r.snapshot_id=$snapshot_id
                      AND r.assertion_status='HUMAN_ASSERTED_UNVERIFIED'
                    RETURN r.local_id AS id,type(r) AS kind,a.local_id AS source,b.local_id AS target,r.evidence_chunk_ids AS evidence_chunk_ids LIMIT 201""",
                    parameters=params,
                )
                edges = [Edge.model_validate(record.data()) async for record in result]
                return Projection(content_hash=projection["hash"], nodes=nodes, edges=edges)
        except GraphFailure:
            raise
        except ValueError as exc:
            raise GraphFailure("GRAPH_INTEGRITY_ERROR") from exc
        except Exception as exc:
            raise GraphFailure("GRAPH_UNAVAILABLE") from exc
