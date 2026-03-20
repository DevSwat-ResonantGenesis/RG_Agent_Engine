"""Create billing and usage tracking tables.

Revision ID: 002_billing
Create Date: 2024-12-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '002_billing'
down_revision = None  # Update this to your latest migration
branch_labels = None
depends_on = None


def upgrade():
    # Organizations table (extends existing or creates new)
    op.create_table(
        'organizations',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('slug', sa.String(255), unique=True, nullable=False),
        sa.Column('stripe_customer_id', sa.String(255), unique=True, nullable=True),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
        sa.Column('plan_tier', sa.String(32), default='free'),
        sa.Column('subscription_status', sa.String(32), default='active'),
        sa.Column('billing_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('billing_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('monthly_token_limit', sa.BigInteger(), default=10000),
        sa.Column('tokens_used_this_period', sa.BigInteger(), default=0),
        sa.Column('overage_tokens_used', sa.BigInteger(), default=0),
        sa.Column('overage_enabled', sa.Boolean(), default=False),
        sa.Column('overage_limit', sa.BigInteger(), nullable=True),
        sa.Column('alert_threshold', sa.Integer(), default=80),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index('ix_organizations_stripe_customer_id', 'organizations', ['stripe_customer_id'])

    # Subscriptions table
    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('stripe_subscription_id', sa.String(255), nullable=True),
        sa.Column('stripe_price_id', sa.String(255), nullable=True),
        sa.Column('plan_tier', sa.String(32), nullable=False),
        sa.Column('status', sa.String(32), default='active'),
        sa.Column('current_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), default=False),
        sa.Column('canceled_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index('ix_subscriptions_org_id', 'subscriptions', ['org_id'])

    # Usage records table
    op.create_table(
        'usage_records',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('usage_type', sa.String(32), nullable=False),
        sa.Column('tokens_consumed', sa.BigInteger(), nullable=False),
        sa.Column('resource_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('resource_name', sa.String(255), nullable=True),
        sa.Column('metadata', postgresql.JSON(), nullable=True),
        sa.Column('billing_period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_usage_records_org_id', 'usage_records', ['org_id'])
    op.create_index('ix_usage_records_user_id', 'usage_records', ['user_id'])
    op.create_index('ix_usage_records_created_at', 'usage_records', ['created_at'])

    # Usage summaries table
    op.create_table(
        'usage_summaries',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('period_type', sa.String(16), nullable=False),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=False),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=False),
        sa.Column('total_tokens', sa.BigInteger(), default=0),
        sa.Column('agent_executions', sa.Integer(), default=0),
        sa.Column('workflow_runs', sa.Integer(), default=0),
        sa.Column('storage_gb', sa.Float(), default=0),
        sa.Column('llm_input_tokens', sa.BigInteger(), default=0),
        sa.Column('llm_output_tokens', sa.BigInteger(), default=0),
        sa.Column('base_tokens_used', sa.BigInteger(), default=0),
        sa.Column('overage_tokens_used', sa.BigInteger(), default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
    )
    op.create_index('ix_usage_summaries_org_id', 'usage_summaries', ['org_id'])

    # Invoices table
    op.create_table(
        'invoices',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('stripe_invoice_id', sa.String(255), unique=True, nullable=True),
        sa.Column('stripe_payment_intent_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(32), default='draft'),
        sa.Column('currency', sa.String(3), default='usd'),
        sa.Column('subtotal', sa.Integer(), default=0),
        sa.Column('tax', sa.Integer(), default=0),
        sa.Column('total', sa.Integer(), default=0),
        sa.Column('amount_paid', sa.Integer(), default=0),
        sa.Column('amount_due', sa.Integer(), default=0),
        sa.Column('period_start', sa.DateTime(timezone=True), nullable=True),
        sa.Column('period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('line_items', postgresql.JSON(), nullable=True),
        sa.Column('invoice_pdf', sa.String(512), nullable=True),
        sa.Column('hosted_invoice_url', sa.String(512), nullable=True),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_invoices_org_id', 'invoices', ['org_id'])

    # Payment methods table
    op.create_table(
        'payment_methods',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('stripe_payment_method_id', sa.String(255), nullable=False),
        sa.Column('card_brand', sa.String(32), nullable=True),
        sa.Column('card_last4', sa.String(4), nullable=True),
        sa.Column('card_exp_month', sa.Integer(), nullable=True),
        sa.Column('card_exp_year', sa.Integer(), nullable=True),
        sa.Column('is_default', sa.Boolean(), default=False),
        sa.Column('is_valid', sa.Boolean(), default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_payment_methods_org_id', 'payment_methods', ['org_id'])

    # Token purchases table
    op.create_table(
        'token_purchases',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('org_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('tokens_purchased', sa.BigInteger(), nullable=False),
        sa.Column('price_paid', sa.Integer(), nullable=False),
        sa.Column('stripe_payment_intent_id', sa.String(255), nullable=True),
        sa.Column('status', sa.String(32), default='pending'),
        sa.Column('tokens_remaining', sa.BigInteger(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_token_purchases_org_id', 'token_purchases', ['org_id'])


def downgrade():
    op.drop_table('token_purchases')
    op.drop_table('payment_methods')
    op.drop_table('invoices')
    op.drop_table('usage_summaries')
    op.drop_table('usage_records')
    op.drop_table('subscriptions')
    op.drop_table('organizations')
