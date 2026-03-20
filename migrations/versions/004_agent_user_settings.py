"""Create agent_user_settings table.

Revision ID: 004_agent_user_settings
Revises: 003_add_autonomous
Create Date: 2026-02-09
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "004_agent_user_settings"
down_revision = "003_add_autonomous"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_user_settings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True, index=True),
        sa.Column("memory_config", postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now(), nullable=True),
    )


def downgrade():
    op.drop_table("agent_user_settings")
