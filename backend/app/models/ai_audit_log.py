from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AIAuditLog(Base):
    __tablename__ = "ai_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tool_or_api_used: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    action_status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )

    @property
    def tool_used(self) -> str | None:
        return self.tool_or_api_used

    @property
    def api_used(self) -> str | None:
        return self.tool_or_api_used

    @property
    def status(self) -> str:
        return self.action_status

    @property
    def created_at(self) -> datetime:
        return self.timestamp
