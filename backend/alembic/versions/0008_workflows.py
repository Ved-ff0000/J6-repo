"""Bounded LangGraph workflow run ledger; no model execution in migrations."""

import sqlalchemy as sa

from alembic import op

revision = "0008_workflows"
down_revision = "0007_providers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.String(50), nullable=False),
        sa.Column("source_checksum", sa.String(64), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("stage", sa.String(40), nullable=False),
        sa.Column("trace", sa.JSON(), nullable=False),
        sa.Column("calls", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error_code", sa.String(50)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "request_key", name="uq_workflow_request_tenant"),
        sa.CheckConstraint(
            "status IN ('STARTED','REVIEW_REQUIRED','FAILED')", name="workflow_status"
        ),
        sa.ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_workflow_version_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_workflow_session_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_workflow_requester_tenant",
        ),
    )
    op.create_index("ix_workflow_runs_organization_id", "workflow_runs", ["organization_id"])
    op.create_index("ix_workflow_runs_document_id", "workflow_runs", ["document_id"])
    op.execute("UPDATE system_metadata SET value='phase9' WHERE key='schema_baseline'")


def downgrade() -> None:
    op.drop_table("workflow_runs")
    op.execute("UPDATE system_metadata SET value='phase8' WHERE key='schema_baseline'")
