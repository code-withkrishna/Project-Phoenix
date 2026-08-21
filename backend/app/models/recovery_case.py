"""Recovery case ORM model."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.types import JSONType


class RecoveryCase(Base):
    """State machine entity tracking a failed payment recovery attempt."""

    __tablename__ = "recovery_cases"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )
    payment_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    customer_email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    customer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DETECTED", index=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    failure_telemetry: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    is_recovered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    recovered_amount: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
