from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.response import error_response
from app.db.session import get_db
from app.models.employee import Employee
from app.schemas.chat import ActionChatRequest, ChatMessageCreate, ChatSessionCreate, PolicyChatRequest, SQLChatRequest
from app.services.ai.action_agent import ActionAgentError, answer_action_question, confirm_hr_action
from app.services.ai.audit import get_ai_audit_logs, log_ai_interaction
from app.services.ai.policy_rag import PolicyRAGError, answer_policy_question
from app.services.ai.sql_agent import SQLAgentError, answer_sql_question
from app.services.auth import get_current_user, oauth2_scheme

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post("/policy")
async def policy_chat(
    payload: PolicyChatRequest,
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await answer_policy_question(payload.message)
        action_status = "REFUSED" if result.get("refused") else "COMPLETED"
        await log_ai_interaction(
            user_id=current_user.id,
            role=current_user.role,
            message=payload.message,
            intent="POLICY_RAG",
            tool_or_api_used="/api/v1/chat/policy",
            action_status=action_status,
            db=db,
        )
    except PolicyRAGError as error:
        logger.warning(
            "policy_rag_request_failed",
            user_id=current_user.id,
            role=current_user.role.value,
            error_code=error.code,
            error_message=error.message,
        )
        await log_ai_interaction(
            user_id=current_user.id,
            role=current_user.role,
            message=payload.message,
            intent="POLICY_RAG",
            tool_or_api_used="/api/v1/chat/policy",
            action_status=f"FAILED_{error.code}",
            db=db,
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error_response(error.code, error.message),
        ) from None
    return {"success": True, "data": result, "error": None}


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
        action_status = "DENIED" if result.get("denied") else "COMPLETED"
        tool_label = result.get("sql") or "/api/v1/chat/sql"
        await log_ai_interaction(
            user_id=current_user.id,
            role=current_user.role,
            message=payload.message,
            intent="SQL_QUERY",
            tool_or_api_used=tool_label,
            action_status=action_status,
            db=db,
        )
    except SQLAgentError as error:
        logger.error(
            "sql_agent_request_failed",
            user_id=current_user.id,
            role=current_user.role.value,
            error_code=error.code,
            error_message=error.message,
        )
        await log_ai_interaction(
            user_id=current_user.id,
            role=current_user.role,
            message=payload.message,
            intent="SQL_QUERY",
            tool_or_api_used="/api/v1/chat/sql",
            action_status=f"FAILED_{error.code}",
            db=db,
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
    db: AsyncSession = Depends(get_db),
):
    try:
        if payload.confirmation_token:
            if payload.message:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_response("INVALID_CONFIRMATION", "Send either a new message or a confirmation, not both."),
                )
            result = await confirm_hr_action(current_user, access_token, payload.confirmation_token)
            action_data = result.get("action") or {}
            tool_name = action_data.get("tool_name", "hr_action_confirm")
            action_status = action_data.get("status", "completed")
            await log_ai_interaction(
                user_id=current_user.id,
                role=current_user.role,
                message=f"CONFIRM_TOKEN:{payload.confirmation_token[:16]}",
                intent="HR_TASK_AUTOMATION_CONFIRM",
                tool_or_api_used=tool_name,
                action_status=action_status,
                db=db,
            )
        elif payload.message:
            result = await answer_action_question(
                payload.message,
                [item.model_dump() for item in payload.history],
                current_user,
                access_token,
            )
            action_data = result.get("action")
            if action_data:
                tool_name = action_data.get("tool_name", "hr_action")
                action_status = action_data.get("status", "proposed")
            else:
                tool_name = "conversational_assistant"
                action_status = "COMPLETED"
            await log_ai_interaction(
                user_id=current_user.id,
                role=current_user.role,
                message=payload.message,
                intent="HR_TASK_AUTOMATION",
                tool_or_api_used=tool_name,
                action_status=action_status,
                db=db,
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
        await log_ai_interaction(
            user_id=current_user.id,
            role=current_user.role,
            message=payload.message or f"CONFIRM:{payload.confirmation_token}",
            intent="HR_TASK_AUTOMATION",
            tool_or_api_used="hr_action",
            action_status=f"FAILED_{error.code}",
            db=db,
        )
        raise HTTPException(
            status_code=error.status_code,
            detail=error_response(error.code, error.message),
        ) from None
    return {"success": True, "data": result, "error": None}


@router.get("/audit-logs")
async def list_ai_audit_logs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    intent: str | None = Query(default=None),
    action_status: str | None = Query(default=None),
    current_user: Employee = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    filter_user_id = None if current_user.role.value == "ADMIN" else current_user.id
    logs = await get_ai_audit_logs(
        db,
        user_id=filter_user_id,
        intent=intent,
        action_status=action_status,
        limit=limit,
        offset=offset,
    )
    return {
        "success": True,
        "data": [
            {
                "id": entry.id,
                "user_id": entry.user_id,
                "role": entry.role,
                "message": entry.message,
                "intent": entry.intent,
                "tool_or_api_used": entry.tool_or_api_used,
                "action_status": entry.action_status,
                "timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
            }
            for entry in logs
        ],
        "error": None,
    }


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
