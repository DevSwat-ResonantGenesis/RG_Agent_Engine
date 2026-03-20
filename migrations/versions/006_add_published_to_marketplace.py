"""Add published_to_marketplace column to agent_definitions.

Revision ID: 006_published_marketplace
Revises: 005_agent_hash_versions
"""

from alembic import op
import sqlalchemy as sa

revision = "006_published_marketplace"
down_revision = "005_agent_hash_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("published_to_marketplace", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade():
    op.drop_column("agent_definitions", "published_to_marketplace")
