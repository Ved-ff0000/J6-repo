"""Source-scoped comparison snapshots with guarded rollback."""

import sqlalchemy as sa

from alembic import op

revision = "0011_conflicts"
down_revision = "0010_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "conflict_comparisons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("left_document_id", sa.Uuid(), nullable=False),
        sa.Column("left_version_id", sa.Uuid(), nullable=False),
        sa.Column("left_run_id", sa.Uuid(), nullable=False),
        sa.Column("right_document_id", sa.Uuid(), nullable=False),
        sa.Column("right_version_id", sa.Uuid(), nullable=False),
        sa.Column("right_run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("left_input_sha256", sa.String(64), nullable=False),
        sa.Column("right_input_sha256", sa.String(64), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("left_run_id <> right_run_id", name="conflict_distinct_runs"),
        sa.CheckConstraint(
            "left_version_id <> right_version_id", name="conflict_distinct_versions"
        ),
        sa.UniqueConstraint(
            "organization_id", "request_key", name="uq_conflict_comparison_request"
        ),
        sa.ForeignKeyConstraint(
            ["left_run_id", "left_document_id", "left_version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_conflict_left_source",
        ),
        sa.ForeignKeyConstraint(
            ["right_run_id", "right_document_id", "right_version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_conflict_right_source",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_conflict_comparison_actor",
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_conflict_comparison_session",
        ),
    )
    op.create_index(
        "ix_conflict_comparisons_organization_id", "conflict_comparisons", ["organization_id"]
    )
    op.create_index("ix_conflict_comparisons_left_run_id", "conflict_comparisons", ["left_run_id"])
    op.create_index(
        "ix_conflict_comparisons_right_run_id", "conflict_comparisons", ["right_run_id"]
    )
    op.execute("UPDATE system_metadata SET value='phase12' WHERE key='schema_baseline'")


def downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM conflict_comparisons) THEN RAISE EXCEPTION 'Preserve conflict comparisons before downgrade'; END IF; END; $$"
        )
    elif not context.as_sql:
        if op.get_bind().scalar(sa.text("SELECT count(*) FROM conflict_comparisons")):
            raise RuntimeError("Preserve conflict comparisons before downgrade")
    else:
        raise RuntimeError("Offline downgrade requires PostgreSQL guard support")
    op.drop_table("conflict_comparisons")
    op.execute("UPDATE system_metadata SET value='phase11' WHERE key='schema_baseline'")
