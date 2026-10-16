"""Durable vector indexing manifests; external generations are published only after completion."""

import sqlalchemy as sa

from alembic import op

revision = "0005_retrieval"
down_revision = "0004_processing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_index_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("generation", sa.Uuid()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(40)),
        sa.Column("chunk_count", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version_id", name="uq_index_version"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','READY','FAILED')", name="index_status"),
        sa.CheckConstraint("attempts >= 0", name="index_attempts"),
        sa.ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_index_version_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_index_requester_tenant",
        ),
    )
    for column in ("organization_id", "status", "lease_expires_at"):
        op.create_index("ix_document_index_jobs_" + column, "document_index_jobs", [column])
    op.execute("UPDATE system_metadata SET value='phase6' WHERE key='schema_baseline'")


def downgrade() -> None:
    op.drop_table("document_index_jobs")
    op.execute("UPDATE system_metadata SET value='phase5' WHERE key='schema_baseline'")
