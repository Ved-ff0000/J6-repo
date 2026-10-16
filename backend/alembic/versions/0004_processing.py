"""Version-bound durable jobs, normalized pages and offset-backed chunks."""

import sqlalchemy as sa

from alembic import op

revision = "0004_processing"
down_revision = "0003_documents"
branch_labels = None
depends_on = None


def identity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
    ]


def version_fk(name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["version_id", "document_id", "organization_id"],
        [
            "document_versions.id",
            "document_versions.document_id",
            "document_versions.organization_id",
        ],
        name=name,
    )


def upgrade() -> None:
    with op.batch_alter_table("document_versions") as batch:
        batch.add_column(
            sa.Column("processing_status", sa.String(20), server_default="UPLOADED", nullable=False)
        )
        batch.add_column(sa.Column("parsed_segment_count", sa.Integer()))
        batch.add_column(sa.Column("page_number_kind", sa.String(20)))
        batch.create_unique_constraint(
            "uq_version_document_tenant", ["id", "document_id", "organization_id"]
        )
    op.create_table(
        "document_processing_jobs",
        *identity_columns(),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("stage", sa.String(30), nullable=False),
        sa.Column("progress", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("claim_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(40)),
        sa.Column("parser_revision", sa.String(50), nullable=False),
        sa.Column("page_number_kind", sa.String(20)),
        sa.Column("segment_count", sa.Integer()),
        sa.Column("chunk_count", sa.Integer()),
        sa.Column("character_count", sa.Integer()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("version_id", name="uq_processing_version"),
        sa.CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')", name="job_status"
        ),
        sa.CheckConstraint("progress >= 0 AND progress <= 100", name="job_progress"),
        sa.CheckConstraint("attempts >= 0", name="job_attempts"),
        version_fk("fk_job_version_tenant"),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_job_requester_tenant",
        ),
    )
    for column in ("organization_id", "document_id", "status", "lease_expires_at"):
        op.create_index(
            f"ix_document_processing_jobs_{column}", "document_processing_jobs", [column]
        )
    op.create_table(
        "document_pages",
        *identity_columns(),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("page_number_kind", sa.String(20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("headings", sa.JSON(), nullable=False),
        sa.Column("tables", sa.JSON(), nullable=False),
        sa.Column("parser_revision", sa.String(50), nullable=False),
        sa.UniqueConstraint("version_id", "page_number", name="uq_version_page"),
        sa.UniqueConstraint(
            "version_id", "organization_id", "page_number", name="uq_page_tenant_number"
        ),
        version_fk("fk_page_version_tenant"),
    )
    op.create_index("ix_document_pages_organization_id", "document_pages", ["organization_id"])
    op.create_table(
        "document_chunks",
        *identity_columns(),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("section", sa.String(150)),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parser_revision", sa.String(50), nullable=False),
        sa.UniqueConstraint("version_id", "chunk_index", name="uq_version_chunk"),
        sa.CheckConstraint("start_offset >= 0 AND end_offset > start_offset", name="chunk_offsets"),
        version_fk("fk_chunk_version_tenant"),
        sa.ForeignKeyConstraint(
            ["version_id", "organization_id", "page_number"],
            [
                "document_pages.version_id",
                "document_pages.organization_id",
                "document_pages.page_number",
            ],
            name="fk_chunk_page_tenant",
        ),
    )
    op.create_index("ix_document_chunks_organization_id", "document_chunks", ["organization_id"])
    op.execute("UPDATE system_metadata SET value='phase5' WHERE key='schema_baseline'")


def downgrade() -> None:
    # Stop workers first. Removing parsed data also resets its public status/physical count.
    op.execute(
        "UPDATE documents SET status='UPLOADED' WHERE status IN ('PROCESSING','PARSING','PARSED','FAILED')"
    )
    op.execute("UPDATE document_versions SET page_count=NULL")
    op.drop_table("document_chunks")
    op.drop_table("document_pages")
    op.drop_table("document_processing_jobs")
    with op.batch_alter_table("document_versions") as batch:
        batch.drop_constraint("uq_version_document_tenant", type_="unique")
        batch.drop_column("page_number_kind")
        batch.drop_column("parsed_segment_count")
        batch.drop_column("processing_status")
    op.execute("UPDATE system_metadata SET value='phase4' WHERE key='schema_baseline'")
