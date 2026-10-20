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
    get_scoped_employee_matches,
    get_scoped_schema,
)

logger = structlog.get_logger(__name__)
_REPORT_SCOPE_PATTERN = re.compile(
    r"\b(?:direct\s+reports?|my\s+reports?|reports?\s+(?:under|of)|team\s+members?|subordinates?|"
    r"employees?\s+reporting\s+to|people\s+reporting\s+to)\b",
    re.IGNORECASE,
)
_PROJECT_ROLE_LIST_PATTERN = re.compile(
    r"\b(?:what|which|list|show)\b.{0,60}\b(?:project\s+roles?|roles?\s+on\s+projects?)\b|"
    r"\b(?:various|different|all)\s+project\s+roles?\b",
    re.IGNORECASE,
)
_EXPLICIT_EMPLOYEE_ID_PATTERN = re.compile(r"\b(?:employee\s+)?id\s*[:#]?\s*\d+\b", re.IGNORECASE)
_EMPLOYEE_ID_QUESTION_PATTERN = re.compile(
    r"\b(?:employees?|users?|persons?|staff(?:\s+members?)?)\s+(?:id|identifier)\b|"
    r"\b(?:id|identifier)\s+(?:of|for)\b|\bwhat(?:'s|\s+is)\b.*\b(?:id|identifier)\b",
    re.IGNORECASE,
)


class SQLAgentError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _named_employee_subject_name(
    message: str,
    named_employee_matches: list[tuple[int, str]],
) -> str | None:
    if (
        len(named_employee_matches) != 1
        or _REPORT_SCOPE_PATTERN.search(message)
        or _EXPLICIT_EMPLOYEE_ID_PATTERN.search(message)
    ):
        return None
    return named_employee_matches[0][1]


def _build_system_prompt(
    role: Role,
    schema: str,
    employee_id: int | None = None,
    named_employee_names: tuple[str, ...] = (),
) -> str:
    permissions = SQL_ACCESS_CONTROL_MATRIX[role]
    identity_context = (
        f"Authenticated employee database ID: {employee_id}. Treat 'me', 'my', and 'own' as this identity. "
        "For managers, 'my team', 'my reports', and 'my employees' refer to direct reports; do not narrow "
        "those requests to the manager's own ID. The server independently enforces the row scope.\n"
        if employee_id is not None
        else ""
    )
    named_employee_context = (
        "Full employee name(s) from the user's authorized records that appear in this request: "
        + ", ".join(named_employee_names)
        + ". Preserve and match the complete name, not just one name component.\n"
        if named_employee_names
        else ""
    )
    return (
        "You are an HRMS text-to-SQL assistant. Generate exactly one SQLite SELECT statement and nothing else. "
        "Use only tables and columns in the provided schema. Never request restricted columns or attempt to bypass "
        "the user's permissions. Do not use comments, semicolons, PRAGMA, or write operations. Use the listed "
        "relationships to join tables; do not guess join keys. Never guess or hard-code an employee ID from a "
        "person's name. When a person is identified by name, join the requested record table to employees using "
        "the listed relationship and filter on employees.name case-insensitively. Use a numeric employee ID only "
        "when the user explicitly gives that ID or when resolving the authenticated user's 'me'/'my' identity. "
        "For skill expertise questions, join employees to "
        "employee_skills through employee_id, then to skills through skill_id and filter by skills.name or "
        "employee_skills.level. For permission violations, do not generate SQL or an empty sentinel query: "
        "return exactly one of these tokens instead: DENIED:OTHER_EMPLOYEE_INFO, DENIED:PAYROLL_DATA, "
        "DENIED:RESTRICTED_FIELD, or DENIED:OUT_OF_SCOPE. Use OTHER_EMPLOYEE_INFO for another person's "
        "records outside this role's scope, PAYROLL_DATA only when payroll rows are outside this role's scope, "
        "and RESTRICTED_FIELD for any blocked field. Payroll is available within the role's payroll scope; do "
        "not refuse payroll queries as a category. Keep queries focused.\n\n"
        f"User role: {role.value}. {role_scope_description(role)}\n"
        f"{identity_context}"
        f"{named_employee_context}"
        f"Employee skill search scope: {permissions.employee_skill_search_scope}. "
        f"Project assignment scope: {permissions.project_assignment_scope}. "
        f"Leave data scope: {permissions.leave_data_scope}. "
        f"Payroll access scope: {permissions.payroll_access}.\n"
        "For case-insensitive employee name lookups, compare LOWER(employees.name) with LOWER(<name>) "
        "or use COLLATE NOCASE; do not use case-sensitive equality for employee names. Preserve exact "
        "case-sensitive enum values from the database guidance.\n"
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
        named_employee_matches = get_scoped_employee_matches(
            database_url,
            current_user.id,
            role,
            message,
        )
    except SQLGuardrailError as error:
        logger.error(
            "sql_agent_schema_unavailable",
            user_id=current_user.id,
            role=role.value,
            error_message=_safe_error_message(error),
        )
        raise SQLAgentError("SQL_AGENT_DATABASE_UNAVAILABLE", str(error), 503) from None

    system_prompt = _build_system_prompt(
        role,
        schema,
        current_user.id,
        tuple(name for _, name in named_employee_matches),
    )
    named_employee_subject_name = _named_employee_subject_name(message, named_employee_matches)

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
            denial_code = _model_denial_code(raw_sql)
            should_retry_manager_denial = role == Role.MANAGER and denial_code in {
                "OTHER_EMPLOYEE_INFO", "PAYROLL_DATA"
            }
            should_retry_admin_denial = role == Role.ADMIN and denial_code == "OTHER_EMPLOYEE_INFO"
            if should_retry_manager_denial or should_retry_admin_denial:
                logger.info(
                    "sql_agent_role_scope_refusal_retry",
                    user_id=current_user.id,
                    role=role.value,
                )
                retry_instruction = (
                    "\nThe request may be within the manager's direct-report payroll scope. Managers "
                    "are allowed to query their own and direct-report payroll rows. Do not refuse only "
                    "because payroll was requested or the request names another employee. Query "
                    "employees joined to payroll_records by "
                    "payroll_records.employee_id = employees.id and match the requested name. Both tables "
                    "are already restricted by the server to this manager's team, so do not add an extra "
                    "manager_id filter. Return the SELECT query; scoped execution will return zero rows if "
                    "the named person is outside the permitted team."
                    if role == Role.MANAGER
                    else "\nThe authenticated user is an admin and may query all rows in allowed HR tables. "
                    "Do not refuse a request merely because it concerns another employee. Generate a SELECT "
                    "using only the supplied schema; do not request restricted fields."
                )
                retry_messages = [
                    {
                        "role": "system",
                        "content": system_prompt + retry_instruction,
                    },
                    *history,
                    {"role": "user", "content": message},
                ]
                generated = await client.chat.completions.create(
                    model=settings.openai_model,
                    temperature=0,
                    messages=retry_messages,
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

    if named_employee_subject_name and _EMPLOYEE_ID_QUESTION_PATTERN.search(message):
        escaped_name = named_employee_subject_name.replace("'", "''")
        raw_sql = (
            "SELECT id, name FROM employees WHERE name = "
            f"'{escaped_name}'"
        )
    else:
        if _PROJECT_ROLE_LIST_PATTERN.search(message) and not named_employee_subject_name:
            raw_sql = (
                "SELECT DISTINCT role_on_project FROM employee_projects "
                "WHERE role_on_project IS NOT NULL ORDER BY role_on_project"
            )

        model_denial = _model_denial_code(raw_sql)
        if model_denial is not None:
            return _denied_result(role, model_denial)

    try:
        safe_sql = clean_generated_sql(
            raw_sql,
            role,
            named_employee_matches,
            named_employee_subject_name,
        )
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
