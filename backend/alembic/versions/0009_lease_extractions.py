"""Persist extraction mode without rewriting or relabeling existing Phase 9 runs."""

import sqlalchemy as sa

from alembic import op

revision = "0009_lease_extractions"
down_revision = "0008_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workflow_runs",
        sa.Column(
            "extraction_mode",
            sa.String(30),
            sa.CheckConstraint(
                "extraction_mode IN ('CANDIDATES','LEASE_FIELDS')", name="workflow_mode"
            ),
            nullable=False,
            server_default="CANDIDATES",
        ),
    )
    op.execute("UPDATE system_metadata SET value='phase10' WHERE key='schema_baseline'")


def downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM workflow_runs WHERE extraction_mode='LEASE_FIELDS') THEN RAISE EXCEPTION 'Preserve and reconcile lease extraction runs before downgrade'; END IF; END; $$;"
        )
    elif not context.as_sql:
        if op.get_bind().scalar(
            sa.text("SELECT count(*) FROM workflow_runs WHERE extraction_mode='LEASE_FIELDS'")
        ):
            raise RuntimeError("Preserve and reconcile lease extraction runs before downgrade")
    else:
        raise RuntimeError("Offline downgrade requires PostgreSQL guard support")
    op.drop_column("workflow_runs", "extraction_mode")
    op.execute("UPDATE system_metadata SET value='phase9' WHERE key='schema_baseline'")
