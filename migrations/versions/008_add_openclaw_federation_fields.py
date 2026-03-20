"""Add OpenClaw federation fields to agent_definitions.

New columns:
- agent_source: 'cloud' (default) or 'openclaw' (runs on user hardware)
- openclaw_config: JSON with endpoint URL, hardware info, capabilities, heartbeat, custom skills

Revision ID: 008_add_openclaw_federation
Revises: 007_add_provider_column
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "008_add_openclaw_federation"
down_revision = "007_add_provider_column"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "agent_definitions",
        sa.Column("agent_source", sa.String(32), server_default="cloud", nullable=False),
    )
    op.add_column(
        "agent_definitions",
        sa.Column("openclaw_config", JSONB, nullable=True),
    )
    op.create_index("ix_agent_definitions_agent_source", "agent_definitions", ["agent_source"])


def downgrade():
    op.drop_index("ix_agent_definitions_agent_source", table_name="agent_definitions")
    op.drop_column("agent_definitions", "openclaw_config")
    op.drop_column("agent_definitions", "agent_source")
