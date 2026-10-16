"""Execute reversible migrations on SQLite too; PostgreSQL drift remains a CI gate."""

import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.db.models import Base


def test_migrations_match_registered_models_and_reverse() -> None:
    engine = create_engine("sqlite://")
    modules = []
    for path in sorted((Path(__file__).parents[1] / "alembic" / "versions").glob("*.py")):
        specification = importlib.util.spec_from_file_location(path.stem, path)
        assert specification is not None and specification.loader is not None
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        modules.append(module)
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        with Operations.context(context):
            for module in modules:
                module.upgrade()
            assert (
                connection.scalar(
                    text("SELECT value FROM system_metadata WHERE key='schema_baseline'")
                )
                == "phase24"
            )
            assert compare_metadata(context, Base.metadata) == []
            for module in reversed(modules):
                module.downgrade()
        assert inspect(connection).get_table_names() == []
    engine.dispose()


def test_lease_mode_migration_preserves_legacy_json_and_guards_unsafe_rollback():
    from datetime import UTC, datetime, timedelta
    from uuid import uuid4

    import pytest
    from sqlalchemy import MetaData, select
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://")
    modules = []
    for path in sorted((Path(__file__).parents[1] / "alembic" / "versions").glob("000[1-9]*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    legacy = {
        "classification": {"kind": "LEASE"},
        "candidates": [],
        "confidence": {"semantic_score": None, "evidence_coverage": None},
        "source_chunk_count": 1,
        "total_chunk_count": 1,
        "retrieved_chunk_count": 1,
        "scope": "TITLE_AND_HEADING_CANDIDATES_ONLY",
        "decision": "REVIEW_REQUIRED",
        "review_queue_created": False,
    }
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        with Operations.context(MigrationContext.configure(connection)):
            for module in modules[:-1]:
                module.upgrade()
            metadata = MetaData()
            metadata.reflect(connection)
            org, user, session, document, version, run = [uuid4().hex for _ in range(6)]
            seed = [
                (
                    "organizations",
                    {
                        "id": org,
                        "name": "Synthetic",
                        "slug": "legacy",
                        "plan": "FREE",
                        "status": "ACTIVE",
                    },
                ),
                (
                    "users",
                    {
                        "id": user,
                        "organization_id": org,
                        "email": "legacy@example.com",
                        "password_hash": "not-a-real-login",
                        "first_name": "A",
                        "last_name": "B",
                        "role": "ORG_ADMIN",
                        "status": "ACTIVE",
                    },
                ),
                (
                    "auth_sessions",
                    {
                        "id": session,
                        "user_id": user,
                        "organization_id": org,
                        "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    },
                ),
                (
                    "documents",
                    {
                        "id": document,
                        "organization_id": org,
                        "uploaded_by": user,
                        "title": "Legacy",
                        "current_version": 1,
                        "status": "UPLOADED",
                        "classification": "UNCLASSIFIED",
                    },
                ),
                (
                    "document_versions",
                    {
                        "id": version,
                        "document_id": document,
                        "organization_id": org,
                        "version_number": 1,
                        "filename": "synthetic.txt",
                        "mime_type": "text/plain",
                        "checksum": "a" * 64,
                        "file_size": 1,
                        "object_key": "synthetic",
                        "uploaded_by": user,
                        "scan_status": "NOT_SCANNED",
                    },
                ),
                (
                    "workflow_runs",
                    {
                        "id": run,
                        "organization_id": org,
                        "requested_by": user,
                        "auth_session_id": session,
                        "document_id": document,
                        "version_id": version,
                        "request_key": uuid4().hex,
                        "revision": "evidence-agent-v1",
                        "source_checksum": "a" * 64,
                        "status": "REVIEW_REQUIRED",
                        "stage": "decision",
                        "trace": [],
                        "calls": [],
                        "result": legacy,
                    },
                ),
            ]
            for table, data in seed:
                connection.execute(metadata.tables[table].insert().values(**data))
            modules[-1].upgrade()
            current = MetaData()
            current.reflect(connection)
            runs = current.tables["workflow_runs"]
            row = connection.execute(select(runs)).mappings().one()
            assert (
                row["extraction_mode"] == "CANDIDATES"
                and row["result"] == legacy
                and row["revision"] == "evidence-agent-v1"
            )
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(runs.update().values(extraction_mode="APPROVED"))
            connection.execute(runs.update().values(extraction_mode="LEASE_FIELDS"))
            with pytest.raises(RuntimeError, match="reconcile"):
                modules[-1].downgrade()
            assert (
                connection.scalar(
                    text("SELECT value FROM system_metadata WHERE key='schema_baseline'")
                )
                == "phase10"
            )
            connection.execute(runs.update().values(extraction_mode="CANDIDATES"))
            spec = importlib.util.spec_from_file_location(
                "rules_migration",
                Path(__file__).parents[1] / "alembic" / "versions" / "0010_rules.py",
            )
            rules_migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(rules_migration)
            rules_migration.upgrade()
            assert connection.execute(select(runs)).mappings().one()["result"] == legacy
            connection.execute(runs.update().values(revision="lease-field-agent-v2"))
            with pytest.raises(RuntimeError, match="Preserve"):
                rules_migration.downgrade()
            connection.execute(runs.update().values(revision="evidence-agent-v1"))
            rules_metadata = MetaData()
            rules_metadata.reflect(connection)
            ledger = rules_metadata.tables["rule_evaluations"]
            connection.execute(
                ledger.insert().values(
                    id=uuid4().hex,
                    organization_id=org,
                    document_id=document,
                    version_id=version,
                    workflow_run_id=run,
                    requested_by=user,
                    auth_session_id=session,
                    request_key=uuid4().hex,
                    input_sha256="a" * 64,
                    policy_sha256="b" * 64,
                    report={"preserved": "snapshot"},
                )
            )
            with pytest.raises(RuntimeError, match="Preserve"):
                rules_migration.downgrade()
            assert connection.scalar(select(ledger.c.report)) == {"preserved": "snapshot"}
            connection.execute(ledger.delete())
            rules_migration.downgrade()
            assert connection.execute(select(runs)).mappings().one()["result"] == legacy
            modules[-1].downgrade()
            assert "extraction_mode" not in {
                column["name"] for column in inspect(connection).get_columns("workflow_runs")
            }
    engine.dispose()


def test_cli_registry_loads_all_models_without_starting_the_api():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.db import registry;from app.db.models import Base;import sys;assert 'app.main' not in sys.modules;assert {'organizations','documents','document_processing_jobs','document_index_jobs','workflow_runs','rule_evaluations','conflict_comparisons'}<=set(Base.metadata.tables);assert all(f.column is not None for t in Base.metadata.tables.values() for f in t.foreign_keys)",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_migrated_audit_table_refuses_updates_and_deletes():
    from datetime import UTC, datetime
    from uuid import uuid4

    import pytest
    from sqlalchemy.exc import IntegrityError

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for path in sorted((Path(__file__).parents[1] / "alembic" / "versions").glob("*.py")):
                spec = importlib.util.spec_from_file_location(path.stem, path)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.upgrade()
        connection.execute(
            text(
                "INSERT INTO audit_events (id,organization_id,sequence,actor_id,event_type,resource_type,resource_id,request_id,payload,previous_sha256,sha256,created_at) VALUES (:id,:org,1,NULL,'TEST','test',:resource,'test','{}',:hash,:hash,:created)"
            ),
            {
                "id": uuid4().hex,
                "org": uuid4().hex,
                "resource": uuid4().hex,
                "hash": "0" * 64,
                "created": datetime.now(UTC),
            },
        )
        for statement in (
            "UPDATE audit_events SET event_type='CHANGED'",
            "DELETE FROM audit_events",
        ):
            with pytest.raises(IntegrityError), connection.begin_nested():
                connection.execute(text(statement))
        assert connection.scalar(text("SELECT count(*) FROM audit_events")) == 1
    engine.dispose()
