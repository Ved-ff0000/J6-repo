"""Tenant-scoped documents, immutable original versions and user-supplied metadata."""

import sqlalchemy as sa

from alembic import op

revision = "0003_documents"
down_revision = "0002_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("parent_document_id", sa.Uuid()),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "UPLOADED",
                "PARSING",
                "PARSED",
                "INDEXING",
                "READY",
                "PROCESSING",
                "COMPLETED",
                "FAILED",
                "ARCHIVED",
                native_enum=False,
                create_constraint=True,
                name="document_status",
            ),
            nullable=False,
        ),
        sa.Column("classification", sa.String(40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("id", "organization_id", name="uq_document_org"),
        sa.CheckConstraint("current_version >= 1", name="document_positive_version"),
        sa.ForeignKeyConstraint(
            ["uploaded_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_document_uploader_org",
        ),
        sa.ForeignKeyConstraint(
            ["parent_document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_document_parent_org",
        ),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_table(
        "document_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("mime_type", sa.String(100), nullable=False),
        sa.Column("checksum", sa.String(64), nullable=False),
        sa.Column("file_size", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(300), nullable=False, unique=True),
        sa.Column("uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("page_count", sa.Integer()),
        sa.Column("scan_status", sa.String(30), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("document_id", "version_number", name="uq_document_version_number"),
        sa.UniqueConstraint("document_id", "checksum", name="uq_document_version_checksum"),
        sa.CheckConstraint("version_number >= 1", name="version_positive_number"),
        sa.CheckConstraint("file_size > 0", name="version_positive_size"),
        sa.CheckConstraint("scan_status = 'NOT_SCANNED'", name="version_scan_status"),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_version_document_org",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_version_uploader_org",
        ),
    )
    op.create_index(
        "ix_document_versions_organization_id", "document_versions", ["organization_id"]
    )
    op.create_index("ix_document_versions_document_id", "document_versions", ["document_id"])
    op.create_table(
        "document_metadata",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(40), nullable=False),
        sa.Column("property_name", sa.String(200)),
        sa.Column("external_reference", sa.String(120)),
        sa.Column("notes", sa.String(2000)),
        sa.UniqueConstraint("document_id", name="uq_document_metadata"),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_metadata_document_org",
        ),
    )
    op.create_index(
        "ix_document_metadata_organization_id", "document_metadata", ["organization_id"]
    )
    op.execute("UPDATE system_metadata SET value = 'phase4' WHERE key = 'schema_baseline'")


def downgrade() -> None:
    op.drop_table("document_metadata")
    op.drop_table("document_versions")
    op.drop_table("documents")
    op.execute("UPDATE system_metadata SET value = 'phase3' WHERE key = 'schema_baseline'")
