"""Recovery case repository."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import case as sql_case, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.recovery_case import RecoveryCase
from app.schemas.ai import CustomerHistoryContext


class RecoveryCaseRepository:
    """Data access for recovery cases and related audit logs."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, case_id: uuid.UUID) -> RecoveryCase | None:
        """Fetch a recovery case by primary key."""
        result = await self._session.execute(
            select(RecoveryCase).where(RecoveryCase.id == case_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id_for_update(self, case_id: uuid.UUID) -> RecoveryCase | None:
        """Fetch a recovery case by primary key with FOR UPDATE row lock."""
        result = await self._session.execute(
            select(RecoveryCase).where(RecoveryCase.id == case_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_by_payment_id(self, payment_id: str) -> RecoveryCase | None:
        """Fetch a recovery case by Razorpay payment ID."""
        result = await self._session.execute(
            select(RecoveryCase).where(RecoveryCase.payment_id == payment_id)
        )
        return result.scalar_one_or_none()

    async def list_cases(
        self,
        *,
        status: str | None = None,
        payment_id: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[RecoveryCase], int]:
        """Return paginated recovery cases with optional filters."""
        query = select(RecoveryCase)
        count_query = select(func.count()).select_from(RecoveryCase)

        if status is not None:
            query = query.where(RecoveryCase.status == status)
            count_query = count_query.where(RecoveryCase.status == status)
        if payment_id is not None:
            query = query.where(RecoveryCase.payment_id == payment_id)
            count_query = count_query.where(RecoveryCase.payment_id == payment_id)

        total_result = await self._session.execute(count_query)
        total = int(total_result.scalar_one())

        offset = (page - 1) * page_size
        query = (
            query.order_by(RecoveryCase.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await self._session.execute(query)
        return list(result.scalars().all()), total

    async def create_if_absent(
        self,
        *,
        payment_id: str,
        order_id: str | None,
        amount: int,
        currency: str,
        customer_email: str | None,
        customer_phone: str | None,
        failure_code: str | None,
        failure_reason: str | None,
        failure_telemetry: dict,
        status: str = "DETECTED",
    ) -> tuple[RecoveryCase | None, bool]:
        """Create a recovery case idempotently. Returns (case, created)."""
        stmt = (
            insert(RecoveryCase)
            .values(
                id=uuid.uuid4(),
                payment_id=payment_id,
                order_id=order_id,
                amount=amount,
                currency=currency,
                customer_email=customer_email,
                customer_phone=customer_phone,
                status=status,
                failure_code=failure_code,
                failure_reason=failure_reason,
                failure_telemetry=failure_telemetry,
            )
            .on_conflict_do_nothing(index_elements=["payment_id"])
            .returning(RecoveryCase)
        )
        result = await self._session.execute(stmt)
        created = result.scalar_one_or_none()
        if created is not None:
            await self._session.commit()
            return created, True

        existing = await self.get_by_payment_id(payment_id)
        return existing, False

    async def update_status(
        self,
        case: RecoveryCase,
        *,
        new_status: str,
        trigger: str,
        actor: str,
        from_state: str | None = None,
        context_metadata: dict | None = None,
    ) -> RecoveryCase:
        """Transition case status and append audit log."""
        previous_status = case.status
        case.status = new_status
        case.updated_at = datetime.now(UTC)
        await self._session.flush()

        audit = AuditLog(
            case_id=case.id,
            from_state=from_state or previous_status,
            to_state=new_status,
            trigger=trigger,
            actor=actor,
            context_metadata=context_metadata or {},
        )
        self._session.add(audit)
        await self._session.commit()
        await self._session.refresh(case)
        return case

    async def append_audit(
        self,
        *,
        case_id: uuid.UUID | None,
        from_state: str | None,
        to_state: str,
        trigger: str,
        actor: str,
        context_metadata: dict | None = None,
    ) -> AuditLog:
        """Append an audit log entry without changing case status."""
        audit = AuditLog(
            case_id=case_id,
            from_state=from_state,
            to_state=to_state,
            trigger=trigger,
            actor=actor,
            context_metadata=context_metadata or {},
        )
        self._session.add(audit)
        await self._session.commit()
        await self._session.refresh(audit)
        return audit

    async def get_audit_trail(self, case_id: uuid.UUID) -> list[AuditLog]:
        """Return audit entries for a case ordered by creation time."""
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.case_id == case_id)
            .order_by(AuditLog.created_at.asc())
        )
        return list(result.scalars().all())

    async def get_customer_history(
        self,
        customer_email: str | None,
        customer_phone: str | None,
        exclude_case_id: uuid.UUID | None = None,
    ) -> CustomerHistoryContext:
        """Aggregate deterministic historical metrics for a customer."""
        or_conditions = []
        if customer_email:
            or_conditions.append(RecoveryCase.customer_email == customer_email)
        if customer_phone:
            or_conditions.append(RecoveryCase.customer_phone == customer_phone)

        if not or_conditions:
            return CustomerHistoryContext(
                prior_failures_today=0,
                lifetime_recoveries=0,
                is_repeat_customer=False,
            )

        customer_filter = or_(*or_conditions)
        if exclude_case_id is not None:
            case_filter = (RecoveryCase.id != exclude_case_id) & customer_filter
        else:
            case_filter = customer_filter

        twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

        stmt = select(
            func.count().label("total_cases"),
            func.coalesce(
                func.sum(
                    sql_case(
                        (RecoveryCase.created_at >= twenty_four_hours_ago, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("prior_failures_today"),
            func.coalesce(
                func.sum(
                    sql_case(
                        (RecoveryCase.is_recovered.is_(True), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("lifetime_recoveries"),
        ).where(case_filter)

        result = await self._session.execute(stmt)
        row = result.one()
        total_cases = int(row.total_cases)
        prior_failures_today = int(row.prior_failures_today)
        lifetime_recoveries = int(row.lifetime_recoveries)

        return CustomerHistoryContext(
            prior_failures_today=prior_failures_today,
            lifetime_recoveries=lifetime_recoveries,
            is_repeat_customer=total_cases > 0,
        )

