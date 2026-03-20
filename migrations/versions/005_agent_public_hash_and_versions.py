"""Add agent_public_hash and agent_versions table.

Revision ID: 005_agent_hash_versions
Revises: 004_agent_user_settings
Create Date: 2026-02-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "005_agent_hash_versions"
down_revision = "004_agent_user_settings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("agent_public_hash", sa.String(length=66), nullable=True),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("agent_version_hash", sa.String(length=66), nullable=True),
    )
    op.create_index(
        "ix_agent_definitions_agent_public_hash",
        "agent_definitions",
        ["agent_public_hash"],
        unique=False,
    )
    op.create_index(
        "ix_agent_definitions_agent_version_hash",
        "agent_definitions",
        ["agent_version_hash"],
        unique=False,
    )

    op.create_table(
        "agent_definition_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_definitions.id"),
            nullable=False,
        ),
        sa.Column("agent_public_hash", sa.String(length=66), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("agent_version_hash", sa.String(length=66), nullable=False),
        sa.Column("changelog", sa.Text(), nullable=True),
        sa.Column("config_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_index("ix_agent_definition_versions_agent_id", "agent_definition_versions", ["agent_id"], unique=False)
    op.create_index(
        "ix_agent_definition_versions_agent_public_hash",
        "agent_definition_versions",
        ["agent_public_hash"],
        unique=False,
    )


def downgrade():
    op.drop_index("ix_agent_definition_versions_agent_public_hash", table_name="agent_definition_versions")
    op.drop_index("ix_agent_definition_versions_agent_id", table_name="agent_definition_versions")
    op.drop_table("agent_definition_versions")

    op.drop_index("ix_agent_definitions_agent_version_hash", table_name="agent_definitions")
    op.drop_index("ix_agent_definitions_agent_public_hash", table_name="agent_definitions")

    op.drop_column("agent_definitions", "agent_version_hash")
    op.drop_column("agent_definitions", "agent_public_hash")
