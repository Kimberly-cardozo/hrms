from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import error_response
from app.db.session import get_db
from app.models.employee import Employee
from app.schemas.chat import ActionChatRequest, ChatMessageCreate, ChatSessionCreate, SQLChatRequest
from app.services.ai.action_agent import ActionAgentError, answer_action_question, confirm_hr_action
from app.services.ai.sql_agent import SQLAgentError, answer_sql_question
from app.services.auth import get_current_user, oauth2_scheme

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


@router.post("/actions")
async def action_chat(
    payload: ActionChatRequest,
    current_user: Employee = Depends(get_current_user),
    access_token: str = Depends(oauth2_scheme),
):
    try:
        if payload.confirmation_token:
            if payload.message:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_response("INVALID_CONFIRMATION", "Send either a new message or a confirmation, not both."),
                )
            result = await confirm_hr_action(current_user, access_token, payload.confirmation_token)
        elif payload.message:
            result = await answer_action_question(
                payload.message,
                [item.model_dump() for item in payload.history],
                current_user,
                access_token,
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_response("INVALID_ACTION_REQUEST", "Provide a message or a confirmation token."),
            )
    except ActionAgentError as error:
        logger.warning(
            "hr_action_request_failed",
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
