from datetime import datetime, timezone
from typing import Any
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.ai_audit_log import AIAuditLog

logger = structlog.get_logger(__name__)


async def log_ai_interaction(
    *,
    user_id: int,
    role: str | Any,
    message: str,
    intent: str,
    action_status: str | None = None,
    tool_or_api_used: str | None = None,
    status: str | None = None,
    tool_used: str | None = None,
    api_used: str | None = None,
    timestamp: datetime | None = None,
    db: AsyncSession | None = None,
) -> AIAuditLog | None:
    """Logs an AI interaction with user details, intent, tool, and action status."""
    resolved_role = role.value if hasattr(role, "value") else str(role)
    resolved_status = action_status or status or "COMPLETED"
    resolved_tool = tool_or_api_used or tool_used or api_used
    resolved_timestamp = timestamp or datetime.now(timezone.utc)

    log_entry = AIAuditLog(
        user_id=user_id,
        role=resolved_role,
        message=message,
        intent=intent,
        tool_or_api_used=resolved_tool,
        action_status=resolved_status,
        timestamp=resolved_timestamp,
    )

    try:
        if db is not None:
            db.add(log_entry)
            await db.commit()
            await db.refresh(log_entry)
        else:
            async with SessionLocal() as session:
                session.add(log_entry)
                await session.commit()
                await session.refresh(log_entry)

        logger.info(
            "ai_audit_logged",
            audit_id=log_entry.id,
            user_id=user_id,
            role=resolved_role,
            intent=intent,
            tool_or_api_used=resolved_tool,
            action_status=resolved_status,
        )
        return log_entry
    except Exception as error:
        logger.error(
            "ai_audit_logging_failed",
            user_id=user_id,
            role=resolved_role,
            intent=intent,
            error_type=type(error).__name__,
            error_message=str(error),
        )
        return None


async def get_ai_audit_logs(
    db: AsyncSession,
    *,
    user_id: int | None = None,
    role: str | None = None,
    intent: str | None = None,
    action_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AIAuditLog]:
    """Retrieve audit logs with optional filtering and pagination."""
    query = select(AIAuditLog).order_by(AIAuditLog.timestamp.desc())
    if user_id is not None:
        query = query.where(AIAuditLog.user_id == user_id)
    if role is not None:
        query = query.where(AIAuditLog.role == role)
    if intent is not None:
        query = query.where(AIAuditLog.intent == intent)
    if action_status is not None:
        query = query.where(AIAuditLog.action_status == action_status)
    query = query.offset(offset).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())
