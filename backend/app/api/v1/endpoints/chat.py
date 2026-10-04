from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import error_response
from app.db.session import get_db
from app.models.employee import Employee
from app.schemas.chat import ChatMessageCreate, ChatSessionCreate, SQLChatRequest
from app.services.ai.sql_agent import SQLAgentError, answer_sql_question
from app.services.auth import get_current_user

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/sql")
async def query_sql(
    payload: SQLChatRequest,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await answer_sql_question(
            message=payload.message,
            history=[message.model_dump() for message in payload.history],
            current_user=current_user,
            database_url=str(db.bind.url),
        )
    except SQLAgentError as error:
        logger.error(
            "sql_agent_request_failed",
            user_id=current_user.id,
            role=current_user.role.value,
            error_code=error.code,
            error_message=error.message,
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error_response(error.code, error.message),
        ) from None
    return {"success": True, "data": result, "error": None}


@router.post("/sessions")
async def create_chat_session(
    payload: ChatSessionCreate,
    current_user: Employee = Depends(get_current_user),
):
    _ = payload
    _ = current_user
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=error_response("CHAT_NOT_IMPLEMENTED", "Chat session creation is a Phase-3 stub and not implemented yet"),
    )


@router.post("/sessions/{session_id}/messages")
async def post_chat_message(
    session_id: str,
    payload: ChatMessageCreate,
    current_user: Employee = Depends(get_current_user),
):
    _ = session_id
    _ = payload
    _ = current_user
    return JSONResponse(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        content=error_response("CHAT_NOT_IMPLEMENTED", "Chat messaging is a Phase-3 stub and not implemented yet"),
    )
