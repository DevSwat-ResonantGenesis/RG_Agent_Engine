"""Add org_id column to agent_definitions for multi-tenant isolation (Phase 4.3).

Revision ID: 009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = "009"
down_revision = "008_add_openclaw_federation_fields"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("org_id", UUID(as_uuid=True), nullable=True, index=True),
    )
    op.create_index("ix_agent_definitions_org_id", "agent_definitions", ["org_id"])


def downgrade():
    op.drop_index("ix_agent_definitions_org_id", table_name="agent_definitions")
    op.drop_column("agent_definitions", "org_id")
