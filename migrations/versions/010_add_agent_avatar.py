"""Add avatar_url column to agent_definitions table for custom agent avatars.

Revision ID: 010_add_agent_avatar
Revises: 009_add_org_id_to_agent_definitions
"""

from alembic import op
import sqlalchemy as sa

revision = "010_add_agent_avatar"
down_revision = "009_add_org_id_to_agent_definitions"
branch_labels = None
depends_on = None


def upgrade():
    # Add avatar_url column to agent_definitions table
    op.add_column(
        "agent_definitions",
        sa.Column("avatar_url", sa.String(512), nullable=True),
    )
    op.create_index(
        "ix_agent_definitions_avatar_url",
        "agent_definitions",
        ["avatar_url"],
    )


def downgrade():
    op.drop_index("ix_agent_definitions_avatar_url", table_name="agent_definitions")
    op.drop_column("agent_definitions", "avatar_url")
