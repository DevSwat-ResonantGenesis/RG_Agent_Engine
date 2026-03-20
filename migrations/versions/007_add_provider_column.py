"""Add provider column to agent_definitions.

Revision ID: 007_add_provider_column
Revises: 006_published_marketplace
"""

from alembic import op
import sqlalchemy as sa

revision = "007_add_provider_column"
down_revision = "006_published_marketplace"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("provider", sa.String(64), nullable=True),
    )


def downgrade():
    op.drop_column("agent_definitions", "provider")
