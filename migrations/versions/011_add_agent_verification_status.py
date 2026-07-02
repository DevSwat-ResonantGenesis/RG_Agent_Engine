"""Add verification/lifecycle status + temporary-provider marking.

Adds a real gate between "a row got saved" and "this agent actually
works" — agents now carry a status (draft/verifying/active/needs_attention)
separate from the existing is_active flag, plus a record of whether their
assigned provider was a temporary substitute for the ideal one.

Revision ID: 011_add_agent_verification_status
Revises: 010_add_agent_avatar
"""

from alembic import op
import sqlalchemy as sa

revision = "011_add_agent_verification_status"
down_revision = "010_add_agent_avatar"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("provider_is_temporary", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("provider_temporary_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("ideal_provider", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_agent_definitions_status",
        "agent_definitions",
        ["status"],
    )
    # Note: agent_teams already has its own status column
    # (active/archived/suspended) — reused as-is, no change needed here.


def downgrade():
    op.drop_index("ix_agent_definitions_status", table_name="agent_definitions")
    op.drop_column("agent_definitions", "ideal_provider")
    op.drop_column("agent_definitions", "provider_temporary_reason")
    op.drop_column("agent_definitions", "provider_is_temporary")
    op.drop_column("agent_definitions", "status")
