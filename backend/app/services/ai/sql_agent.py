import re

from openai import AsyncOpenAI
import structlog

from app.core.config import settings
from app.models.employee import Employee
from app.models.enums import Role
from app.services.ai.permissions import (
    SQL_ACCESS_CONTROL_MATRIX,
    SQL_DENIAL_CODES,
    SQL_SCHEMA_RELATIONSHIPS,
    empty_result_message,
    guardrail_denial_message,
    role_scope_description,
    sql_value_guidance,
)
from app.services.ai.sql_guardrails import (
    MAX_RESULT_ROWS,
    SQLGuardrailError,
    clean_generated_sql,
    execute_scoped_select,
    get_scoped_schema,
)

logger = structlog.get_logger(__name__)


class SQLAgentError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _build_system_prompt(role: Role, schema: str) -> str:
    permissions = SQL_ACCESS_CONTROL_MATRIX[role]
    return (
        "You are an HRMS text-to-SQL assistant. Generate exactly one SQLite SELECT statement and nothing else. "
        "Use only tables and columns in the provided schema. Never request restricted columns or attempt to bypass "
        "the user's permissions. Do not use comments, semicolons, PRAGMA, or write operations. Use the listed "
        "relationships to join tables; do not guess join keys. For skill expertise questions, join employees to "
        "employee_skills through employee_id, then to skills through skill_id and filter by skills.name or "
        "employee_skills.level. For permission violations, do not generate SQL or an empty sentinel query: "
        "return exactly one of these tokens instead: DENIED:OTHER_EMPLOYEE_INFO, DENIED:PAYROLL_DATA, "
        "DENIED:RESTRICTED_FIELD, or DENIED:OUT_OF_SCOPE. Use OTHER_EMPLOYEE_INFO for another person's "
        "records outside this role's scope, PAYROLL_DATA for payroll, and RESTRICTED_FIELD for any blocked "
        "field. Keep queries focused.\n\n"
        f"User role: {role.value}. {role_scope_description(role)}\n"
        f"Employee skill search scope: {permissions.employee_skill_search_scope}. "
        f"Project assignment scope: {permissions.project_assignment_scope}. "
        f"Leave data scope: {permissions.leave_data_scope}.\n"
        f"Database value and SQLite guidance:\n{sql_value_guidance()}\n"
        "Available scoped schema:\n"
        f"{schema}\n"
        "Verified table relationships:\n"
        + "\n".join(f"- {relationship}" for relationship in SQL_SCHEMA_RELATIONSHIPS)
    )


def _safe_error_message(error: Exception) -> str:
    message = str(error)
    if settings.openai_api_key:
        message = message.replace(settings.openai_api_key, "[REDACTED]")
    return message


def _denied_result(role: Role, code: str) -> dict:
    answer = guardrail_denial_message(code, role)
    logger.warning("sql_agent_permission_denied", role=role.value, denial_code=code)
    return {"answer": answer, "sql": None, "rows": [], "truncated": False, "denied": True}


def _model_denial_code(response: str) -> str | None:
    match = re.fullmatch(r"DENIED:([A-Z_]+)", response.strip(), flags=re.IGNORECASE)
    if match:
        code = match.group(1).upper()
        if code in SQL_DENIAL_CODES:
            return code
    if re.fullmatch(r"SELECT\s+1\s+WHERE\s+0;?", response.strip(), flags=re.IGNORECASE):
        return "OUT_OF_SCOPE"
    return None


def _empty_result(role: Role, sql: str) -> dict:
    answer = empty_result_message(role)
    logger.info("sql_agent_empty_result", role=role.value, sql=sql)
    logger.info("sql_agent_response_ready", role=role.value, answer=answer)
    return {
        "answer": answer,
        "sql": sql if SQL_ACCESS_CONTROL_MATRIX[role].view_raw_sql else None,
        "rows": [],
        "truncated": False,
        "denied": False,
    }


def _matched_result(role: Role, sql: str, rows: list[dict], truncated: bool) -> dict:
    if truncated:
        answer = f"More than {MAX_RESULT_ROWS} matching rows were found; showing the first {MAX_RESULT_ROWS}."
    else:
        answer = f"The query returned {len(rows)} matching result row{'s' if len(rows) != 1 else ''}."
    logger.info("sql_agent_response_ready", role=role.value, answer=answer)
    return {
        "answer": answer,
        "sql": sql if SQL_ACCESS_CONTROL_MATRIX[role].view_raw_sql else None,
        "rows": rows,
        "truncated": truncated,
        "denied": False,
    }


async def answer_sql_question(
    message: str,
    history: list[dict[str, str]],
    current_user: Employee,
    database_url: str,
) -> dict:
    role = current_user.role
    logger.info(
        "sql_agent_message_received",
        user_id=current_user.id,
        role=role.value,
        message=message,
    )

    if not settings.openai_api_key.strip():
        logger.error("sql_agent_not_configured", user_id=current_user.id, role=role.value)
        raise SQLAgentError(
            "SQL_AGENT_NOT_CONFIGURED",
            "The SQL assistant is not configured. Set OPENAI_API_KEY in backend/.env.",
            503,
        )

    try:
        schema = get_scoped_schema(database_url, current_user.id, role)
    except SQLGuardrailError as error:
        logger.error(
            "sql_agent_schema_unavailable",
            user_id=current_user.id,
            role=role.value,
            error_message=_safe_error_message(error),
        )
        raise SQLAgentError("SQL_AGENT_DATABASE_UNAVAILABLE", str(error), 503) from None

    system_prompt = _build_system_prompt(role, schema)

    try:
        async with AsyncOpenAI(api_key=settings.openai_api_key) as client:
            generated = await client.chat.completions.create(
                model=settings.openai_model,
                temperature=0,
                messages=[
                    {"role": "system", "content": system_prompt},
                    *history,
                    {"role": "user", "content": message},
                ],
            )
            raw_sql = generated.choices[0].message.content or ""
    except Exception as error:
        logger.error(
            "sql_agent_generation_failed",
            user_id=current_user.id,
            role=role.value,
            error_type=type(error).__name__,
            error_message=_safe_error_message(error),
        )
        raise SQLAgentError(
            "SQL_AGENT_UPSTREAM_ERROR",
            "The SQL assistant could not contact the language model. Check the OpenAI configuration and try again.",
            502,
        ) from error

    model_denial = _model_denial_code(raw_sql)
    if model_denial is not None:
        return _denied_result(role, model_denial)

    try:
        safe_sql = clean_generated_sql(raw_sql, role)
        logger.info(
            "sql_agent_query_generated",
            user_id=current_user.id,
            role=role.value,
            sql=safe_sql,
        )
        rows, truncated = execute_scoped_select(database_url, current_user.id, role, safe_sql)
        logger.info(
            "sql_agent_query_results",
            user_id=current_user.id,
            role=role.value,
            row_count=len(rows),
            truncated=truncated,
            rows=rows,
        )
    except SQLGuardrailError as error:
        logger.warning(
            "sql_agent_query_rejected",
            user_id=current_user.id,
            role=role.value,
            error_message=_safe_error_message(error),
        )
        if error.code == "RESTRICTED_FIELD":
            return _denied_result(role, "RESTRICTED_FIELD")
        if error.code == "DISALLOWED_TABLE":
            denial_code = "PAYROLL_DATA" if error.table_name == "payroll_records" else "OUT_OF_SCOPE"
            return _denied_result(role, denial_code)
        raise SQLAgentError(
            "SQL_AGENT_QUERY_REJECTED",
            "I could not generate a safe read-only query for that question. Try rephrasing it.",
            400,
        ) from None

    if not rows:
        return _empty_result(role, safe_sql)
    return _matched_result(role, safe_sql, rows, truncated)
