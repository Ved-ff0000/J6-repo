"""Immutable source-backed manual graph revisions and durable projection manifests."""

import sqlalchemy as sa

from alembic import op

revision = "0006_graph"
down_revision = "0005_retrieval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_graph_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("assertions", sa.JSON(), nullable=False),
        sa.Column("evidence_hashes", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Uuid()),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(40)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version_id", "revision", name="uq_graph_version_revision"),
        sa.CheckConstraint("revision >= 1", name="graph_revision_positive"),
        sa.CheckConstraint("attempts >= 0", name="graph_attempts"),
        sa.CheckConstraint("status IN ('QUEUED','RUNNING','READY','FAILED')", name="graph_status"),
        sa.ForeignKeyConstraint(
            ["version_id", "document_id", "organization_id"],
            [
                "document_versions.id",
                "document_versions.document_id",
                "document_versions.organization_id",
            ],
            name="fk_graph_version_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["created_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_graph_creator_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_graph_requester_tenant",
        ),
    )
    for column in ("organization_id", "status", "lease_expires_at"):
        op.create_index(
            "ix_document_graph_snapshots_" + column, "document_graph_snapshots", [column]
        )
    op.execute("UPDATE system_metadata SET value='phase7' WHERE key='schema_baseline'")


def downgrade() -> None:
    op.drop_table("document_graph_snapshots")
    op.execute("UPDATE system_metadata SET value='phase6' WHERE key='schema_baseline'")
