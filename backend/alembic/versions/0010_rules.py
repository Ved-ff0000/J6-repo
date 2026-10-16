"""Append-only rule evaluation snapshots and scoped parent identity index."""

import sqlalchemy as sa

from alembic import op

revision = "0010_rules"
down_revision = "0009_lease_extractions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_workflow_run_source",
        "workflow_runs",
        ["id", "document_id", "version_id", "organization_id"],
        unique=True,
    )
    op.create_table(
        "rule_evaluations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("auth_session_id", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("input_sha256", sa.String(64), nullable=False),
        sa.Column("policy_sha256", sa.String(64), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("organization_id", "request_key", name="uq_rule_evaluation_request"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id", "document_id", "version_id", "organization_id"],
            [
                "workflow_runs.id",
                "workflow_runs.document_id",
                "workflow_runs.version_id",
                "workflow_runs.organization_id",
            ],
            name="fk_rule_evaluation_workflow_source",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_rule_evaluation_actor",
        ),
        sa.ForeignKeyConstraint(
            ["auth_session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            name="fk_rule_evaluation_session",
        ),
    )
    op.create_index("ix_rule_evaluations_organization_id", "rule_evaluations", ["organization_id"])
    op.create_index("ix_rule_evaluations_workflow_run_id", "rule_evaluations", ["workflow_run_id"])
    op.execute("UPDATE system_metadata SET value='phase11' WHERE key='schema_baseline'")


def downgrade() -> None:
    context = op.get_context()
    if context.dialect.name == "postgresql":
        op.execute(
            "DO $$ BEGIN IF EXISTS (SELECT 1 FROM rule_evaluations) OR EXISTS (SELECT 1 FROM workflow_runs WHERE revision='lease-field-agent-v2') THEN RAISE EXCEPTION 'Preserve rule evaluations and rule-enabled runs before downgrade'; END IF; END; $$"
        )
    elif not context.as_sql:
        if op.get_bind().scalar(
            sa.text(
                "SELECT (SELECT count(*) FROM rule_evaluations)+(SELECT count(*) FROM workflow_runs WHERE revision='lease-field-agent-v2')"
            )
        ):
            raise RuntimeError("Preserve rule evaluations and rule-enabled runs before downgrade")
    else:
        raise RuntimeError("Offline downgrade requires PostgreSQL guard support")
    op.drop_table("rule_evaluations")
    op.drop_index("uq_workflow_run_source", table_name="workflow_runs")
    op.execute("UPDATE system_metadata SET value='phase10' WHERE key='schema_baseline'")
