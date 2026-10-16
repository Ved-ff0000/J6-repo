"""Organizations, tenant-bound users and revocable token families."""

import sqlalchemy as sa

from alembic import op

revision = "0002_identity"
down_revision = "0001_foundation"
branch_labels = None
depends_on = None


def timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(80), nullable=False, unique=True),
        sa.Column("plan", sa.String(30), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        *timestamps(),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name="org_status"),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(500), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=False),
        sa.Column("last_name", sa.String(100), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "SUPER_ADMIN",
                "ORG_ADMIN",
                "REVIEWER",
                "ANALYST",
                "VIEWER",
                "AUDITOR",
                native_enum=False,
                create_constraint=True,
                name="user_role",
            ),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        *timestamps(),
        sa.UniqueConstraint("id", "organization_id", name="uq_user_org"),
        sa.CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="user_status"),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("id", "organization_id", name="uq_session_org"),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            ondelete="CASCADE",
            name="fk_session_user_org",
        ),
    )
    op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
    op.create_index("ix_auth_sessions_organization_id", "auth_sessions", ["organization_id"])
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id", "organization_id"],
            ["auth_sessions.id", "auth_sessions.organization_id"],
            ondelete="CASCADE",
            name="fk_refresh_session_org",
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("used_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_refresh_tokens_session_id", "refresh_tokens", ["session_id"])
    op.create_index("ix_refresh_tokens_organization_id", "refresh_tokens", ["organization_id"])
    op.execute("UPDATE system_metadata SET value = 'phase3' WHERE key = 'schema_baseline'")


def downgrade() -> None:
    op.drop_table("refresh_tokens")
    op.drop_table("auth_sessions")
    op.drop_table("users")
    op.drop_table("organizations")
    op.execute("UPDATE system_metadata SET value = 'phase2' WHERE key = 'schema_baseline'")
