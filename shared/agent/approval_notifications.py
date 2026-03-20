"""
Approval Notification System
============================

Notification system for GOVERNED mode approval requests.

Supports:
- In-app notifications
- Email notifications
- Webhook notifications
- Push notifications (placeholder)

Integrates with the approval gate to notify approvers when actions require approval.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable, Awaitable
from datetime import datetime
from enum import Enum
import asyncio
import logging
import json

logger = logging.getLogger(__name__)


class NotificationChannel(str, Enum):
    """Available notification channels."""
    IN_APP = "in_app"
    EMAIL = "email"
    WEBHOOK = "webhook"
    PUSH = "push"
    SLACK = "slack"
    DISCORD = "discord"


class NotificationPriority(str, Enum):
    """Notification priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class NotificationType(str, Enum):
    """Types of notifications."""
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_EXPIRED = "approval_expired"
    WALLET_LOW_BALANCE = "wallet_low_balance"
    WALLET_FROZEN = "wallet_frozen"
    CONTRACT_PENDING = "contract_pending"
    CONTRACT_COMPLETED = "contract_completed"
    CONTRACT_BREACHED = "contract_breached"
    GOAL_COMPLETED = "goal_completed"
    MODE_CHANGED = "mode_changed"


@dataclass
class Notification:
    """A notification to be sent."""
    id: str
    type: NotificationType
    priority: NotificationPriority
    
    # Target
    recipient_id: str  # user_id
    recipient_email: Optional[str] = None
    
    # Content
    title: str = ""
    message: str = ""
    action_url: Optional[str] = None
    action_label: Optional[str] = None
    
    # Context
    agent_id: Optional[str] = None
    approval_id: Optional[str] = None
    contract_id: Optional[str] = None
    
    # Metadata
    data: Dict[str, Any] = field(default_factory=dict)
    
    # Status
    channels: List[NotificationChannel] = field(default_factory=list)
    sent_at: Optional[str] = None
    read_at: Optional[str] = None
    
    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    expires_at: Optional[str] = None


@dataclass
class NotificationPreferences:
    """User notification preferences."""
    user_id: str
    
    # Channel preferences
    enabled_channels: List[NotificationChannel] = field(
        default_factory=lambda: [NotificationChannel.IN_APP, NotificationChannel.EMAIL]
    )
    
    # Type preferences
    enabled_types: List[NotificationType] = field(
        default_factory=lambda: list(NotificationType)
    )
    
    # Priority threshold
    min_priority: NotificationPriority = NotificationPriority.LOW
    
    # Email settings
    email: Optional[str] = None
    email_digest: bool = False  # Batch emails instead of immediate
    
    # Webhook settings
    webhook_url: Optional[str] = None
    webhook_secret: Optional[str] = None
    
    # Quiet hours
    quiet_hours_start: Optional[int] = None  # Hour (0-23)
    quiet_hours_end: Optional[int] = None


class ApprovalNotificationService:
    """
    Service for sending approval-related notifications.
    
    Integrates with:
    - In-app notification system
    - Email service
    - Webhook endpoints
    - Push notification service
    """
    
    def __init__(
        self,
        email_sender: Optional[Callable[[str, str, str], Awaitable[bool]]] = None,
        webhook_sender: Optional[Callable[[str, Dict], Awaitable[bool]]] = None,
        push_sender: Optional[Callable[[str, str, str], Awaitable[bool]]] = None,
    ):
        self.email_sender = email_sender
        self.webhook_sender = webhook_sender
        self.push_sender = push_sender
        
        # In-memory storage (replace with database in production)
        self._notifications: Dict[str, Notification] = {}
        self._user_notifications: Dict[str, List[str]] = {}  # user_id -> notification_ids
        self._preferences: Dict[str, NotificationPreferences] = {}
        
        # Approver registry
        self._approvers: List[str] = []  # user_ids who can approve
        self._approver_emails: Dict[str, str] = {}  # user_id -> email
    
    def register_approver(
        self,
        user_id: str,
        email: Optional[str] = None,
        webhook_url: Optional[str] = None,
    ):
        """Register a user as an approver."""
        if user_id not in self._approvers:
            self._approvers.append(user_id)
        
        if email:
            self._approver_emails[user_id] = email
        
        # Set up preferences
        if user_id not in self._preferences:
            channels = [NotificationChannel.IN_APP]
            if email:
                channels.append(NotificationChannel.EMAIL)
            if webhook_url:
                channels.append(NotificationChannel.WEBHOOK)
            
            self._preferences[user_id] = NotificationPreferences(
                user_id=user_id,
                enabled_channels=channels,
                email=email,
                webhook_url=webhook_url,
            )
    
    def get_approvers(self) -> List[str]:
        """Get list of registered approvers."""
        return self._approvers.copy()
    
    async def notify_approval_required(
        self,
        approval_id: str,
        agent_id: str,
        action: str,
        amount: float,
        description: str,
        risk_level: str,
        expires_at: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """
        Notify all approvers that an action requires approval.
        
        Returns list of notification IDs.
        """
        notification_ids = []
        
        # Determine priority based on risk and amount
        if risk_level == "critical" or amount > 500:
            priority = NotificationPriority.URGENT
        elif risk_level == "high" or amount > 100:
            priority = NotificationPriority.HIGH
        else:
            priority = NotificationPriority.NORMAL
        
        # Create notification for each approver
        for approver_id in self._approvers:
            notification = Notification(
                id=f"notif_{approval_id}_{approver_id}",
                type=NotificationType.APPROVAL_REQUIRED,
                priority=priority,
                recipient_id=approver_id,
                recipient_email=self._approver_emails.get(approver_id),
                title=f"Approval Required: {action}",
                message=f"Agent action requires approval: {description}\nAmount: ${amount:.2f}\nRisk: {risk_level}",
                action_url=f"/approvals/{approval_id}",
                action_label="Review & Approve",
                agent_id=agent_id,
                approval_id=approval_id,
                data={
                    "action": action,
                    "amount": amount,
                    "risk_level": risk_level,
                    "context": context or {},
                },
                expires_at=expires_at,
            )
            
            await self._send_notification(notification)
            notification_ids.append(notification.id)
        
        logger.info(f"Sent approval notifications to {len(self._approvers)} approvers")
        return notification_ids
    
    async def notify_approval_granted(
        self,
        approval_id: str,
        agent_id: str,
        approved_by: str,
        action: str,
    ):
        """Notify that an approval was granted."""
        # Notify the agent owner (if different from approver)
        notification = Notification(
            id=f"notif_approved_{approval_id}",
            type=NotificationType.APPROVAL_GRANTED,
            priority=NotificationPriority.NORMAL,
            recipient_id=agent_id,  # Agent owner
            title=f"Approval Granted: {action}",
            message=f"Your agent's action has been approved by {approved_by}",
            agent_id=agent_id,
            approval_id=approval_id,
        )
        
        await self._send_notification(notification)
    
    async def notify_approval_rejected(
        self,
        approval_id: str,
        agent_id: str,
        rejected_by: str,
        action: str,
        reason: str,
    ):
        """Notify that an approval was rejected."""
        notification = Notification(
            id=f"notif_rejected_{approval_id}",
            type=NotificationType.APPROVAL_REJECTED,
            priority=NotificationPriority.HIGH,
            recipient_id=agent_id,  # Agent owner
            title=f"Approval Rejected: {action}",
            message=f"Your agent's action was rejected: {reason}",
            agent_id=agent_id,
            approval_id=approval_id,
            data={"reason": reason, "rejected_by": rejected_by},
        )
        
        await self._send_notification(notification)
    
    async def notify_approval_expired(
        self,
        approval_id: str,
        agent_id: str,
        action: str,
    ):
        """Notify that an approval request expired."""
        notification = Notification(
            id=f"notif_expired_{approval_id}",
            type=NotificationType.APPROVAL_EXPIRED,
            priority=NotificationPriority.NORMAL,
            recipient_id=agent_id,
            title=f"Approval Expired: {action}",
            message="The approval request has expired without a decision",
            agent_id=agent_id,
            approval_id=approval_id,
        )
        
        await self._send_notification(notification)
    
    async def notify_wallet_low_balance(
        self,
        agent_id: str,
        owner_id: str,
        balance: float,
        threshold: float,
    ):
        """Notify when wallet balance is low."""
        notification = Notification(
            id=f"notif_low_balance_{agent_id}_{datetime.utcnow().timestamp()}",
            type=NotificationType.WALLET_LOW_BALANCE,
            priority=NotificationPriority.HIGH,
            recipient_id=owner_id,
            title="Low Wallet Balance",
            message=f"Agent wallet balance (${balance:.2f}) is below threshold (${threshold:.2f})",
            agent_id=agent_id,
            action_url=f"/agents/{agent_id}/wallet",
            action_label="Add Funds",
        )
        
        await self._send_notification(notification)
    
    async def notify_contract_pending(
        self,
        contract_id: str,
        parties: List[str],
        total_value: float,
        description: str,
    ):
        """Notify parties about a pending contract."""
        for party_id in parties:
            notification = Notification(
                id=f"notif_contract_{contract_id}_{party_id}",
                type=NotificationType.CONTRACT_PENDING,
                priority=NotificationPriority.HIGH,
                recipient_id=party_id,
                title="Contract Pending Approval",
                message=f"A contract worth ${total_value:.2f} requires your review: {description}",
                contract_id=contract_id,
                action_url=f"/contracts/{contract_id}",
                action_label="Review Contract",
            )
            
            await self._send_notification(notification)
    
    async def notify_mode_changed(
        self,
        agent_id: str,
        owner_id: str,
        from_mode: str,
        to_mode: str,
        changed_by: str,
    ):
        """Notify when agent mode is changed."""
        priority = NotificationPriority.URGENT if to_mode == "unbounded" else NotificationPriority.NORMAL
        
        notification = Notification(
            id=f"notif_mode_{agent_id}_{datetime.utcnow().timestamp()}",
            type=NotificationType.MODE_CHANGED,
            priority=priority,
            recipient_id=owner_id,
            title=f"Agent Mode Changed to {to_mode.upper()}",
            message=f"Agent mode changed from {from_mode} to {to_mode} by {changed_by}",
            agent_id=agent_id,
            data={
                "from_mode": from_mode,
                "to_mode": to_mode,
                "changed_by": changed_by,
            },
        )
        
        await self._send_notification(notification)
    
    async def _send_notification(self, notification: Notification):
        """Send a notification through configured channels."""
        prefs = self._preferences.get(notification.recipient_id)
        
        # Default channels if no preferences
        channels = prefs.enabled_channels if prefs else [NotificationChannel.IN_APP]
        notification.channels = channels
        
        # Store notification
        self._notifications[notification.id] = notification
        if notification.recipient_id not in self._user_notifications:
            self._user_notifications[notification.recipient_id] = []
        self._user_notifications[notification.recipient_id].append(notification.id)
        
        # Send through each channel
        tasks = []
        
        if NotificationChannel.IN_APP in channels:
            # In-app is stored above, no additional action needed
            pass
        
        if NotificationChannel.EMAIL in channels and notification.recipient_email:
            if self.email_sender:
                tasks.append(self._send_email(notification))
        
        if NotificationChannel.WEBHOOK in channels and prefs and prefs.webhook_url:
            if self.webhook_sender:
                tasks.append(self._send_webhook(notification, prefs.webhook_url))
        
        if NotificationChannel.PUSH in channels:
            if self.push_sender:
                tasks.append(self._send_push(notification))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        notification.sent_at = datetime.utcnow().isoformat()
        logger.debug(f"Notification {notification.id} sent via {channels}")
    
    async def _send_email(self, notification: Notification) -> bool:
        """Send email notification."""
        if not self.email_sender or not notification.recipient_email:
            return False
        
        try:
            subject = notification.title
            body = f"""
{notification.message}

{f'Action: {notification.action_url}' if notification.action_url else ''}

---
This is an automated notification from ResonantGenesis Agent Engine.
            """.strip()
            
            return await self.email_sender(
                notification.recipient_email,
                subject,
                body,
            )
        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")
            return False
    
    async def _send_webhook(self, notification: Notification, webhook_url: str) -> bool:
        """Send webhook notification."""
        if not self.webhook_sender:
            return False
        
        try:
            payload = {
                "id": notification.id,
                "type": notification.type.value,
                "priority": notification.priority.value,
                "title": notification.title,
                "message": notification.message,
                "agent_id": notification.agent_id,
                "approval_id": notification.approval_id,
                "data": notification.data,
                "created_at": notification.created_at,
                "expires_at": notification.expires_at,
            }
            
            return await self.webhook_sender(webhook_url, payload)
        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
            return False
    
    async def _send_push(self, notification: Notification) -> bool:
        """Send push notification."""
        if not self.push_sender:
            return False
        
        try:
            return await self.push_sender(
                notification.recipient_id,
                notification.title,
                notification.message,
            )
        except Exception as e:
            logger.error(f"Failed to send push notification: {e}")
            return False
    
    def get_user_notifications(
        self,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50,
    ) -> List[Notification]:
        """Get notifications for a user."""
        notification_ids = self._user_notifications.get(user_id, [])
        notifications = [
            self._notifications[nid]
            for nid in notification_ids
            if nid in self._notifications
        ]
        
        if unread_only:
            notifications = [n for n in notifications if n.read_at is None]
        
        # Sort by created_at descending
        notifications.sort(key=lambda n: n.created_at, reverse=True)
        
        return notifications[:limit]
    
    def mark_as_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        notification = self._notifications.get(notification_id)
        if notification:
            notification.read_at = datetime.utcnow().isoformat()
            return True
        return False
    
    def get_unread_count(self, user_id: str) -> int:
        """Get count of unread notifications for a user."""
        notifications = self.get_user_notifications(user_id, unread_only=True)
        return len(notifications)
    
    def set_preferences(self, preferences: NotificationPreferences):
        """Set notification preferences for a user."""
        self._preferences[preferences.user_id] = preferences
    
    def get_preferences(self, user_id: str) -> Optional[NotificationPreferences]:
        """Get notification preferences for a user."""
        return self._preferences.get(user_id)


# Global instance
approval_notification_service = ApprovalNotificationService()


def get_approval_notification_service() -> ApprovalNotificationService:
    """Get the global approval notification service."""
    return approval_notification_service
