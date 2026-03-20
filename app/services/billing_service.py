"""Billing Service - Stripe integration for subscriptions and payments."""

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from uuid import UUID
import logging
import os

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models_billing import (
    Organization, Subscription, Invoice, PaymentMethod, TokenPurchase,
    PlanTier, SubscriptionStatus, PLAN_TOKENS, PLAN_PRICES
)

logger = logging.getLogger(__name__)

# Initialize Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# Stripe Price IDs (set these in your Stripe dashboard)
# Aligned with frontend pricing: Developer (free), Plus ($49), Enterprise (custom)
STRIPE_PRICE_IDS = {
    PlanTier.DEVELOPER: None,  # Free tier - no Stripe price
    PlanTier.PLUS: os.getenv("STRIPE_PRICE_PLUS", "price_plus"),
    PlanTier.ENTERPRISE: os.getenv("STRIPE_PRICE_ENTERPRISE", "price_enterprise"),
    # Legacy aliases
    PlanTier.FREE: None,
    PlanTier.PRO: os.getenv("STRIPE_PRICE_PLUS", "price_plus"),  # Alias for PLUS
}

# Token pack prices
TOKEN_PACK_PRICES = {
    "100k": {"tokens": 100_000, "price": 900, "stripe_price": os.getenv("STRIPE_PRICE_TOKENS_100K", "")},
    "500k": {"tokens": 500_000, "price": 4000, "stripe_price": os.getenv("STRIPE_PRICE_TOKENS_500K", "")},
    "1m": {"tokens": 1_000_000, "price": 7000, "stripe_price": os.getenv("STRIPE_PRICE_TOKENS_1M", "")},
    "5m": {"tokens": 5_000_000, "price": 30000, "stripe_price": os.getenv("STRIPE_PRICE_TOKENS_5M", "")},
}


class BillingService:
    """Service for managing billing, subscriptions, and payments."""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def get_or_create_org(self, org_id: UUID, name: Optional[str] = None) -> Organization:
        """Get or create organization in billing database."""
        org = await self.db.get(Organization, org_id)
        if not org:
            # Auto-create org for billing (synced from auth_service)
            org = Organization(
                id=org_id,
                name=name or f"Organization {str(org_id)[:8]}",
                slug=str(org_id).replace("-", "")[:32],
                plan_tier=PlanTier.FREE.value,
                monthly_token_limit=PLAN_TOKENS[PlanTier.FREE],
                tokens_used_this_period=0,
            )
            self.db.add(org)
            await self.db.commit()
            await self.db.refresh(org)
            logger.info(f"Auto-created organization {org_id} for billing")
        return org
    
    async def get_or_create_stripe_customer(
        self,
        org_id: UUID,
        email: str,
        name: Optional[str] = None,
    ) -> str:
        """Get or create Stripe customer for organization."""
        org = await self.get_or_create_org(org_id, name)
        
        if org.stripe_customer_id:
            return org.stripe_customer_id
        
        # Create Stripe customer
        customer = stripe.Customer.create(
            email=email,
            name=name or org.name,
            metadata={
                "org_id": str(org_id),
                "org_name": org.name,
            }
        )
        
        org.stripe_customer_id = customer.id
        await self.db.commit()
        
        return customer.id
    
    async def create_checkout_session(
        self,
        org_id: UUID,
        plan_tier: PlanTier,
        success_url: str,
        cancel_url: str,
        email: str,
    ) -> Dict[str, Any]:
        """Create Stripe Checkout session for subscription."""
        if plan_tier == PlanTier.FREE:
            raise ValueError("Cannot create checkout for free plan")
        
        # Auto-create org if needed
        org = await self.get_or_create_org(org_id)
        
        # Get or create customer
        customer_id = await self.get_or_create_stripe_customer(org_id, email, org.name)
        
        # Get price ID
        price_id = STRIPE_PRICE_IDS.get(plan_tier)
        if not price_id:
            raise ValueError(f"No Stripe price configured for {plan_tier}")
        
        # Create checkout session
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price": price_id,
                "quantity": 1,
            }],
            mode="subscription",
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={
                "org_id": str(org_id),
                "plan_tier": plan_tier.value,
            },
            subscription_data={
                "metadata": {
                    "org_id": str(org_id),
                    "plan_tier": plan_tier.value,
                }
            }
        )
        
        return {
            "session_id": session.id,
            "checkout_url": session.url,
        }
    
    async def create_token_purchase_session(
        self,
        org_id: UUID,
        pack_id: str,
        success_url: str,
        cancel_url: str,
        email: str,
    ) -> Dict[str, Any]:
        """Create checkout session for token pack purchase."""
        pack = TOKEN_PACK_PRICES.get(pack_id)
        if not pack:
            raise ValueError(f"Invalid token pack: {pack_id}")
        
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        
        customer_id = await self.get_or_create_stripe_customer(org_id, email, org.name)
        
        # Create checkout session for one-time payment
        session = stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"{pack_id.upper()} Token Pack",
                        "description": f"{pack['tokens']:,} Platform Tokens",
                    },
                    "unit_amount": pack["price"],
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=success_url + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=cancel_url,
            metadata={
                "org_id": str(org_id),
                "pack_id": pack_id,
                "tokens": str(pack["tokens"]),
                "type": "token_purchase",
            },
        )
        
        return {
            "session_id": session.id,
            "checkout_url": session.url,
        }
    
    async def handle_subscription_created(
        self,
        subscription: stripe.Subscription,
    ) -> None:
        """Handle successful subscription creation from webhook."""
        org_id = subscription.metadata.get("org_id")
        plan_tier = subscription.metadata.get("plan_tier")
        
        if not org_id:
            logger.error("No org_id in subscription metadata")
            return
        
        org = await self.db.get(Organization, UUID(org_id))
        if not org:
            logger.error(f"Organization {org_id} not found")
            return
        
        # Update organization
        org.stripe_subscription_id = subscription.id
        org.plan_tier = plan_tier
        org.subscription_status = SubscriptionStatus.ACTIVE.value
        org.monthly_token_limit = PLAN_TOKENS.get(PlanTier(plan_tier), 10000)
        org.billing_period_start = datetime.fromtimestamp(subscription.current_period_start)
        org.billing_period_end = datetime.fromtimestamp(subscription.current_period_end)
        org.tokens_used_this_period = 0
        org.overage_tokens_used = 0
        
        # Create subscription record
        sub_record = Subscription(
            org_id=UUID(org_id),
            stripe_subscription_id=subscription.id,
            stripe_price_id=subscription["items"]["data"][0]["price"]["id"] if subscription["items"]["data"] else None,
            plan_tier=plan_tier,
            status=SubscriptionStatus.ACTIVE.value,
            current_period_start=datetime.fromtimestamp(subscription.current_period_start),
            current_period_end=datetime.fromtimestamp(subscription.current_period_end),
        )
        self.db.add(sub_record)
        
        await self.db.commit()
        logger.info(f"Subscription created for org {org_id}: {plan_tier}")
    
    async def handle_subscription_updated(
        self,
        subscription: stripe.Subscription,
    ) -> None:
        """Handle subscription update from webhook."""
        org_id = subscription.metadata.get("org_id")
        if not org_id:
            return
        
        org = await self.db.get(Organization, UUID(org_id))
        if not org:
            return
        
        # Update billing period
        org.billing_period_start = datetime.fromtimestamp(subscription.current_period_start)
        org.billing_period_end = datetime.fromtimestamp(subscription.current_period_end)
        
        # Reset usage on period change
        if subscription.status == "active":
            org.tokens_used_this_period = 0
            org.overage_tokens_used = 0
        
        # Update status
        status_map = {
            "active": SubscriptionStatus.ACTIVE,
            "past_due": SubscriptionStatus.PAST_DUE,
            "canceled": SubscriptionStatus.CANCELED,
            "trialing": SubscriptionStatus.TRIALING,
            "paused": SubscriptionStatus.PAUSED,
        }
        org.subscription_status = status_map.get(
            subscription.status, SubscriptionStatus.ACTIVE
        ).value
        
        await self.db.commit()
    
    async def handle_subscription_canceled(
        self,
        subscription: stripe.Subscription,
    ) -> None:
        """Handle subscription cancellation."""
        org_id = subscription.metadata.get("org_id")
        if not org_id:
            return
        
        org = await self.db.get(Organization, UUID(org_id))
        if not org:
            return
        
        # Downgrade to free
        org.plan_tier = PlanTier.FREE.value
        org.subscription_status = SubscriptionStatus.CANCELED.value
        org.monthly_token_limit = PLAN_TOKENS[PlanTier.FREE]
        org.stripe_subscription_id = None
        
        await self.db.commit()
        logger.info(f"Subscription canceled for org {org_id}")
    
    async def handle_invoice_paid(
        self,
        invoice: stripe.Invoice,
    ) -> None:
        """Handle successful invoice payment."""
        customer_id = invoice.customer
        
        # Find organization by customer ID
        result = await self.db.execute(
            select(Organization).where(Organization.stripe_customer_id == customer_id)
        )
        org = result.scalar_one_or_none()
        if not org:
            return
        
        # Create invoice record
        invoice_record = Invoice(
            org_id=org.id,
            stripe_invoice_id=invoice.id,
            stripe_payment_intent_id=invoice.payment_intent,
            status="paid",
            subtotal=invoice.subtotal,
            tax=invoice.tax or 0,
            total=invoice.total,
            amount_paid=invoice.amount_paid,
            amount_due=invoice.amount_due,
            period_start=datetime.fromtimestamp(invoice.period_start) if invoice.period_start else None,
            period_end=datetime.fromtimestamp(invoice.period_end) if invoice.period_end else None,
            invoice_pdf=invoice.invoice_pdf,
            hosted_invoice_url=invoice.hosted_invoice_url,
            paid_at=datetime.utcnow(),
        )
        self.db.add(invoice_record)
        await self.db.commit()
    
    async def handle_checkout_completed(
        self,
        session: stripe.checkout.Session,
    ) -> None:
        """Handle completed checkout session."""
        session_type = session.metadata.get("type")
        
        if session_type == "token_purchase":
            await self._handle_token_purchase(session)
    
    async def _handle_token_purchase(
        self,
        session: stripe.checkout.Session,
    ) -> None:
        """Process token pack purchase."""
        org_id = session.metadata.get("org_id")
        tokens = int(session.metadata.get("tokens", 0))
        
        if not org_id or not tokens:
            return
        
        org = await self.db.get(Organization, UUID(org_id))
        if not org:
            return
        
        # Create purchase record
        purchase = TokenPurchase(
            org_id=UUID(org_id),
            tokens_purchased=tokens,
            price_paid=session.amount_total,
            stripe_payment_intent_id=session.payment_intent,
            status="completed",
            tokens_remaining=tokens,
        )
        self.db.add(purchase)
        
        # Add tokens to organization (as bonus beyond monthly limit)
        # These tokens don't expire with billing period
        org.monthly_token_limit += tokens
        
        await self.db.commit()
        logger.info(f"Token purchase completed for org {org_id}: {tokens} tokens")
    
    async def cancel_subscription(
        self,
        org_id: UUID,
        at_period_end: bool = True,
    ) -> Dict[str, Any]:
        """Cancel organization's subscription."""
        org = await self.db.get(Organization, org_id)
        if not org or not org.stripe_subscription_id:
            raise ValueError("No active subscription")
        
        if at_period_end:
            # Cancel at end of billing period
            subscription = stripe.Subscription.modify(
                org.stripe_subscription_id,
                cancel_at_period_end=True,
            )
        else:
            # Cancel immediately
            subscription = stripe.Subscription.delete(org.stripe_subscription_id)
        
        return {
            "status": "canceled" if not at_period_end else "cancel_scheduled",
            "cancel_at": subscription.cancel_at,
        }
    
    async def change_plan(
        self,
        org_id: UUID,
        new_plan: PlanTier,
    ) -> Dict[str, Any]:
        """Change subscription plan."""
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        
        if new_plan == PlanTier.FREE:
            # Downgrade to free - cancel subscription
            if org.stripe_subscription_id:
                await self.cancel_subscription(org_id, at_period_end=False)
            org.plan_tier = PlanTier.FREE.value
            org.monthly_token_limit = PLAN_TOKENS[PlanTier.FREE]
            await self.db.commit()
            return {"status": "downgraded", "plan": PlanTier.FREE.value}
        
        if not org.stripe_subscription_id:
            raise ValueError("No active subscription to modify")
        
        # Get new price ID
        new_price_id = STRIPE_PRICE_IDS.get(new_plan)
        if not new_price_id:
            raise ValueError(f"No price configured for {new_plan}")
        
        # Update subscription
        subscription = stripe.Subscription.retrieve(org.stripe_subscription_id)
        stripe.Subscription.modify(
            org.stripe_subscription_id,
            items=[{
                "id": subscription["items"]["data"][0]["id"],
                "price": new_price_id,
            }],
            proration_behavior="create_prorations",
        )
        
        # Update organization
        org.plan_tier = new_plan.value
        org.monthly_token_limit = PLAN_TOKENS.get(new_plan, 10000)
        await self.db.commit()
        
        return {
            "status": "upgraded" if PLAN_PRICES[new_plan] > PLAN_PRICES[PlanTier(org.plan_tier)] else "downgraded",
            "plan": new_plan.value,
        }
    
    async def get_billing_info(self, org_id: UUID) -> Dict[str, Any]:
        """Get organization's billing information."""
        org = await self.db.get(Organization, org_id)
        if not org:
            raise ValueError(f"Organization {org_id} not found")
        
        # Get payment methods
        payment_methods = []
        if org.stripe_customer_id:
            methods = stripe.PaymentMethod.list(
                customer=org.stripe_customer_id,
                type="card",
            )
            payment_methods = [{
                "id": m.id,
                "brand": m.card.brand,
                "last4": m.card.last4,
                "exp_month": m.card.exp_month,
                "exp_year": m.card.exp_year,
            } for m in methods.data]
        
        # Get recent invoices
        invoices = []
        result = await self.db.execute(
            select(Invoice)
            .where(Invoice.org_id == org_id)
            .order_by(Invoice.created_at.desc())
            .limit(10)
        )
        for inv in result.scalars():
            invoices.append({
                "id": str(inv.id),
                "status": inv.status,
                "total": inv.total,
                "paid_at": inv.paid_at.isoformat() if inv.paid_at else None,
                "pdf_url": inv.invoice_pdf,
            })
        
        return {
            "plan_tier": org.plan_tier,
            "subscription_status": org.subscription_status,
            "billing_period_start": org.billing_period_start.isoformat() if org.billing_period_start else None,
            "billing_period_end": org.billing_period_end.isoformat() if org.billing_period_end else None,
            "monthly_token_limit": org.monthly_token_limit,
            "tokens_used": org.tokens_used_this_period,
            "overage_enabled": org.overage_enabled,
            "payment_methods": payment_methods,
            "recent_invoices": invoices,
        }


async def process_stripe_webhook(
    db: AsyncSession,
    payload: bytes,
    signature: str,
) -> Dict[str, Any]:
    """Process Stripe webhook event."""
    try:
        event = stripe.Webhook.construct_event(
            payload, signature, STRIPE_WEBHOOK_SECRET
        )
    except stripe.error.SignatureVerificationError:
        raise ValueError("Invalid webhook signature")
    
    service = BillingService(db)
    
    event_type = event["type"]
    data = event["data"]["object"]
    
    if event_type == "customer.subscription.created":
        await service.handle_subscription_created(data)
    elif event_type == "customer.subscription.updated":
        await service.handle_subscription_updated(data)
    elif event_type == "customer.subscription.deleted":
        await service.handle_subscription_canceled(data)
    elif event_type == "invoice.paid":
        await service.handle_invoice_paid(data)
    elif event_type == "checkout.session.completed":
        await service.handle_checkout_completed(data)
    
    return {"status": "processed", "event_type": event_type}
