"""Recovery Action repository for database operations."""

import logging
from datetime import datetime, timezone
from typing import Sequence
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recovery_action import RecoveryAction
from app.models.recovery_case import RecoveryCase

logger = logging.getLogger(__name__)


ACTION_STATE_TRANSITIONS = {
    "PENDING": frozenset({"ISSUED", "FAILED", "CANCELLED", "EXPIRED"}),
    "ISSUED": frozenset({"PAID", "FAILED", "CANCELLED", "EXPIRED"}),
    "PAID": frozenset(),
    "FAILED": frozenset(),
    "CANCELLED": frozenset(),
    "EXPIRED": frozenset(),
}

class InvalidRecoveryActionTransition(ValueError):
    """Raised when a RecoveryAction transition is not allowed."""


class RecoveryActionRepository:
    """Repository for managing RecoveryAction entities."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        case_id: UUID,
        reference_id: str,
        amount: int,
        currency: str,
        expires_at: datetime,
        action_type: str = "CREATE_PAYMENT_LINK",
        attempt_number: int = 1,
        status: str = "PENDING",
        payment_link_id: str | None = None,
        payment_link_url: str | None = None,
        executed_at: datetime | None = None,
    ) -> RecoveryAction:
        """Create and persist a new RecoveryAction record."""
        action = RecoveryAction(
            case_id=case_id,
            action_type=action_type,
            reference_id=reference_id,
            payment_link_id=payment_link_id,
            payment_link_url=payment_link_url,
            amount=amount,
            currency=currency,
            status=status,
            attempt_number=attempt_number,
            expires_at=expires_at,
            executed_at=executed_at,
        )
        self._session.add(action)
        await self._session.flush()
        return action

    async def get_by_id(self, action_id: UUID) -> RecoveryAction | None:
        """Fetch a RecoveryAction by its primary key ID."""
        result = await self._session.execute(
            select(RecoveryAction).where(RecoveryAction.id == action_id)
        )
        return result.scalar_one_or_none()

    async def get_by_reference_id(self, reference_id: str) -> RecoveryAction | None:
        """Fetch a RecoveryAction by its deterministic reference ID."""
        result = await self._session.execute(
            select(RecoveryAction).where(RecoveryAction.id == reference_id)  # safety fallback
            if False else select(RecoveryAction).where(RecoveryAction.reference_id == reference_id)
        )
        return result.scalar_one_or_none()

    async def get_by_payment_link_id(self, payment_link_id: str) -> RecoveryAction | None:
        """Fetch a RecoveryAction by Razorpay's payment_link_id."""
        result = await self._session.execute(
            select(RecoveryAction).where(RecoveryAction.payment_link_id == payment_link_id)
        )
        return result.scalar_one_or_none()

    async def list_by_case_id(self, case_id: UUID) -> Sequence[RecoveryAction]:
        """List all RecoveryActions associated with a case ID in chronological order."""
        result = await self._session.execute(
            select(RecoveryAction)
            .where(RecoveryAction.case_id == case_id)
            .order_by(RecoveryAction.created_at.asc())
        )
        return result.scalars().all()

    async def list_by_customer(
        self,
        *,
        customer_email: str | None,
        customer_phone: str | None,
        exclude_case_id: UUID | None = None,
    ) -> Sequence[RecoveryAction]:
        """List recovery actions belonging to the same customer across cases."""
        identity_filters = []
        if customer_email:
            identity_filters.append(RecoveryCase.customer_email == customer_email)
        if customer_phone:
            identity_filters.append(RecoveryCase.customer_phone == customer_phone)
        if not identity_filters:
            return []

        query = (
            select(RecoveryAction)
            .join(RecoveryCase, RecoveryCase.id == RecoveryAction.case_id)
            .where(or_(*identity_filters))
            .order_by(RecoveryAction.created_at.asc())
        )
        if exclude_case_id is not None:
            query = query.where(RecoveryAction.case_id != exclude_case_id)

        result = await self._session.execute(query)
        return result.scalars().all()

    async def get_active_action_for_case(self, case_id: UUID) -> RecoveryAction | None:
        """Fetch any active (PENDING or ISSUED) RecoveryAction for a case."""
        result = await self._session.execute(
            select(RecoveryAction).where(
                RecoveryAction.case_id == case_id,
                RecoveryAction.status.in_(["PENDING", "ISSUED"]),
            )
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        action: RecoveryAction,
        new_status: str,
        *,
        payment_link_id: str | None = None,
        payment_link_url: str | None = None,
        executed_at: datetime | None = None,
    ) -> RecoveryAction:
        """Update status and attributes of a RecoveryAction."""
        previous_status = action.status
        if previous_status != new_status:
            allowed_states = ACTION_STATE_TRANSITIONS.get(previous_status)
            if allowed_states is None or new_status not in allowed_states:
                raise InvalidRecoveryActionTransition(
                    f"Invalid RecoveryAction transition: {previous_status} -> {new_status}"
                )
        action.status = new_status
        if payment_link_id is not None:
            action.payment_link_id = payment_link_id
        if payment_link_url is not None:
            action.payment_link_url = payment_link_url
        if executed_at is not None:
            action.executed_at = executed_at
        action.updated_at = datetime.now(timezone.utc)
        await self._session.flush()
        return action
