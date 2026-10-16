"""Tenant-scoped synthetic provider diagnostic metadata and idempotent request ledger."""

import sqlalchemy as sa

from alembic import op

revision = "0007_providers"
down_revision = "0006_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("request_key", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("profile", sa.String(64), nullable=False),
        sa.Column("configured_model", sa.String(120), nullable=False),
        sa.Column("returned_model", sa.String(200)),
        sa.Column("model_version", sa.String(200)),
        sa.Column("provider_response_id", sa.String(200)),
        sa.Column("prompt_name", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("prompt_sha256", sa.String(64), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer()),
        sa.Column("output_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("output_sha256", sa.String(64)),
        sa.Column("error_code", sa.String(50)),
        sa.Column("duration_ms", sa.Float()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("organization_id", "request_key", name="uq_provider_request_tenant"),
        sa.CheckConstraint(
            "status IN ('STARTED','SUCCEEDED','FAILED')", name="provider_run_status"
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0", name="provider_input_tokens"
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0", name="provider_output_tokens"
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0", name="provider_total_tokens"
        ),
        sa.ForeignKeyConstraint(
            ["requested_by", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_provider_requester_tenant",
        ),
    )
    op.create_index("ix_provider_runs_organization_id", "provider_runs", ["organization_id"])
    op.execute("UPDATE system_metadata SET value='phase8' WHERE key='schema_baseline'")


def downgrade() -> None:
    op.drop_table("provider_runs")
    op.execute("UPDATE system_metadata SET value='phase7' WHERE key='schema_baseline'")
