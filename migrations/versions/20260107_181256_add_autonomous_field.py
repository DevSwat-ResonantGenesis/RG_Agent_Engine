"""Add autonomous field to agent_definitions table.

Revision ID: 003_add_autonomous
Create Date: 2026-01-08
"""

from alembic import op
import sqlalchemy as sa

revision = '003_add_autonomous'
down_revision = '002_billing'
branch_labels = None
depends_on = None


def upgrade():
    # Add autonomous column to agent_definitions table
    op.add_column('agent_definitions', sa.Column('autonomous', sa.Boolean(), nullable=False, server_default='false'))


def downgrade():
    # Remove autonomous column from agent_definitions table
    op.drop_column('agent_definitions', 'autonomous')
