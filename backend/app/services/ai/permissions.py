from dataclasses import dataclass
from typing import Literal

from app.models.enums import (
    EmployeeStatus,
    HalfDayPeriod,
    LeaveRequestStatus,
    LeaveType,
    ProjectStatus,
    Role,
    SkillLevel,
    TicketCategory,
    TicketPriority,
    TicketStatus,
)

SQL_ALLOWED_TABLES = (
    "employees",
    "projects",
    "employee_projects",
    "departments",
    "skills",
    "employee_skills",
    "job_history",
    "leave_balances",
    "leave_requests",
    "tickets",
    "payroll_records",
)

SQL_BLOCKED_COLUMNS = frozenset(
    {
        "hashed_password",
        "bank_account_number",
        "bank_account_name",
        "bank_branch",
        "bank_ifsc",
        "pan",
        "pan_number",
        "pan_name",
        "pan_dob",
        "pf_uan",
        "esi_no",
        "date_of_birth",
        "current_salary_usd",
        "profile_photo_path",
        "profile_photo_mime",
    }
)

RecordScope = Literal["self", "team", "all"]


@dataclass(frozen=True)
class SQLRolePermissions:
    row_scope: RecordScope
    project_assignment_scope: RecordScope
    employee_skill_search_scope: RecordScope
    leave_data_scope: RecordScope
    view_raw_sql: bool
    payroll_access: RecordScope


SQL_ACCESS_CONTROL_MATRIX: dict[Role, SQLRolePermissions] = {
    Role.EMPLOYEE: SQLRolePermissions(
        row_scope="self",
        project_assignment_scope="self",
        employee_skill_search_scope="self",
        leave_data_scope="self",
        view_raw_sql=False,
        payroll_access="self",
    ),
    Role.MANAGER: SQLRolePermissions(
        row_scope="team",
        project_assignment_scope="team",
        employee_skill_search_scope="team",
        leave_data_scope="team",
        view_raw_sql=True,
        payroll_access="team",
    ),
    Role.ADMIN: SQLRolePermissions(
        row_scope="all",
        project_assignment_scope="all",
        employee_skill_search_scope="all",
        leave_data_scope="all",
        view_raw_sql=True,
        payroll_access="all",
    ),
}

SQL_TABLES_BY_ROLE: dict[Role, frozenset[str]] = {
    role: frozenset(SQL_ALLOWED_TABLES) for role in Role
}

ACTION_COMMON_TOOLS = frozenset(
    {
        "apply_leave",
        "check_leave_balance",
        "check_my_leave_requests",
        "create_ticket",
        "check_my_tickets",
        "list_hr_policies",
        "handoff_query_assistant",
        "handoff_policy_assistant",
    }
)

ACTION_MANAGER_TOOLS = frozenset(
    {
        "list_pending_leave_requests",
        "approve_leave",
        "reject_leave",
        "assign_ticket",
        "update_ticket_status",
        "create_announcement",
        "assign_employee_project",
    }
)

ACTION_ADMIN_TOOLS = frozenset(
    {
        "create_project",
        "deactivate_employee",
        "reactivate_employee",
        "upload_hr_policy",
        "summarize_hr_policy",
    }
)

ACTION_TOOLS_BY_ROLE: dict[Role, frozenset[str]] = {
    Role.EMPLOYEE: ACTION_COMMON_TOOLS,
    Role.MANAGER: ACTION_COMMON_TOOLS | ACTION_MANAGER_TOOLS,
    Role.ADMIN: ACTION_COMMON_TOOLS | ACTION_MANAGER_TOOLS | ACTION_ADMIN_TOOLS,
}

SHARED_CATALOG_TABLES = frozenset({"projects", "departments", "skills"})

SQL_SCHEMA_RELATIONSHIPS = (
    "employees.department_id = departments.id",
    "employees.manager_id = employees.id",
    "employee_projects.employee_id = employees.id",
    "employee_projects.project_id = projects.id",
    "employee_skills.employee_id = employees.id",
    "employee_skills.skill_id = skills.id",
    "job_history.employee_id = employees.id",
    "leave_balances.employee_id = employees.id",
    "leave_requests.employee_id = employees.id",
    "tickets.employee_id = employees.id",
    "tickets.assignee_id = employees.id",
    "payroll_records.employee_id = employees.id",
)

SQL_ENUM_COLUMN_VALUES = {
    "employees.role": tuple(value.value for value in Role),
    "employees.status": tuple(value.value for value in EmployeeStatus),
    "projects.status": tuple(value.value for value in ProjectStatus),
    "employee_skills.level": tuple(value.value for value in SkillLevel),
    "leave_balances.leave_type": tuple(value.value for value in LeaveType),
    "leave_requests.leave_type": tuple(value.value for value in LeaveType),
    "leave_requests.status": tuple(value.value for value in LeaveRequestStatus),
    "leave_requests.half_day_period": tuple(value.value for value in HalfDayPeriod),
    "tickets.category": tuple(value.value for value in TicketCategory),
    "tickets.priority": tuple(value.value for value in TicketPriority),
    "tickets.status": tuple(value.value for value in TicketStatus),
}

SQL_BOOLEAN_COLUMNS = ("job_history.is_current", "leave_requests.is_half_day")
SQL_DENIAL_CODES = frozenset(
    {"OTHER_EMPLOYEE_INFO", "PAYROLL_DATA", "RESTRICTED_FIELD", "OUT_OF_SCOPE"}
)


def sql_role_permissions(role: Role) -> SQLRolePermissions:
    return SQL_ACCESS_CONTROL_MATRIX[role]


def role_scope_description(role: Role) -> str:
    if role == Role.ADMIN:
        return "Admins may query all rows in allowed HR tables, including payroll amount fields, except blocked bank, PAN, password, and tax identifiers."
    if role == Role.MANAGER:
        return (
            "Managers may query their own and direct-report rows for employee, project assignment, "
            "skill, leave, job-history, ticket, and payroll data. Payroll is limited to the manager and direct reports. "
            "Project, department, and skill catalogs are shared."
        )
    return (
        "Employees may query only their own employee, project assignment, skill, leave, job-history, "
        "ticket, and own payroll records. Payroll is limited to the signed-in employee. Project, department, "
        "and skill catalogs are shared. Employee skill searches "
        "must be limited to the signed-in employee."
    )


def sql_value_guidance() -> str:
    enum_hints = [
        f"- {column}: {', '.join(values)}"
        for column, values in SQL_ENUM_COLUMN_VALUES.items()
    ]
    boolean_columns = ", ".join(SQL_BOOLEAN_COLUMNS)
    return (
        "Case-sensitive enum values; use these exact uppercase literals and do not lowercase them:\n"
        + "\n".join(enum_hints)
        + "\n"
        + f"SQLite boolean columns ({boolean_columns}) use 0 for false and 1 for true. "
        + "Use IS NULL or IS NOT NULL for missing values, never '= NULL'. "
        + "Qualify columns such as id, status, and employee_id with table aliases in joins. "
        + "The projects.status default is ONGOING; still filter with its exact enum literal."
    )


def row_scope_clause(table: str, role: Role, employee_id: int) -> str | None:
    if table in SHARED_CATALOG_TABLES:
        return None

    permissions = sql_role_permissions(role)
    if table == "employee_projects":
        scope = permissions.project_assignment_scope
    elif table == "employee_skills":
        scope = permissions.employee_skill_search_scope
    elif table in {"leave_balances", "leave_requests"}:
        scope = permissions.leave_data_scope
    elif table == "payroll_records":
        scope = permissions.payroll_access
    else:
        scope = permissions.row_scope
    if scope == "all":
        return None
    if table == "employees":
        if scope == "self":
            return f'"id" = {employee_id}'
        return (
            f'"id" IN (SELECT "id" FROM main."employees" '
            f'WHERE "id" = {employee_id} OR "manager_id" = {employee_id})'
        )
    if table == "tickets":
        if scope == "self":
            return f'"employee_id" = {employee_id} OR "assignee_id" = {employee_id}'
        return (
            f'"employee_id" IN (SELECT "id" FROM main."employees" '
            f'WHERE "id" = {employee_id} OR "manager_id" = {employee_id}) '
            f'OR "assignee_id" = {employee_id}'
        )
    if scope == "self":
        return f'"employee_id" = {employee_id}'
    return (
        f'"employee_id" IN (SELECT "id" FROM main."employees" '
        f'WHERE "id" = {employee_id} OR "manager_id" = {employee_id})'
    )


def permission_denial_message(code: str, role: Role) -> str:
    if code == "RESTRICTED_FIELD":
        return "The requested fields cannot be accessed by the SQL assistant for any role."
    if code == "PAYROLL_DATA":
        if role == Role.EMPLOYEE:
            return "You may access your own payroll records only, not another employee's payroll."
        if role == Role.MANAGER:
            return "You may access payroll records for yourself and your direct reports only."
        return "That payroll request uses fields outside the SQL assistant's allowed payroll schema."
    if code == "OTHER_EMPLOYEE_INFO":
        if role == Role.EMPLOYEE:
            return "You do not have permission to view another employee's information. You can ask about your own records."
        if role == Role.MANAGER:
            return "You do not have permission to view information outside your team. Manager access is limited to your own and direct-report records."
        return "Admins may view other employee records and allowed payroll fields; restricted fields remain unavailable."
    if role == Role.EMPLOYEE:
        return "You do not have permission to search other employees' records. You can query your own records and shared catalogs."
    if role == Role.MANAGER:
        return "You do not have permission to view records outside your team. Manager access is limited to your own and direct-report records."
    return "That request is outside the SQL assistant's allowed data scope."


def guardrail_denial_message(code: str, role: Role) -> str:
    if code == "RESTRICTED_FIELD":
        return "The requested fields cannot be accessed by the SQL assistant for any role."
    if code == "PAYROLL_DATA":
        if role == Role.EMPLOYEE:
            return "You may view your own payroll records only."
        if role == Role.MANAGER:
            return "You may view payroll records for yourself and your direct reports only."
        return "Payroll access is limited to allowed payroll fields; bank, PAN, password, and tax identifiers remain blocked."
    return permission_denial_message(code, role)


def empty_result_message(role: Role) -> str:
    if role == Role.EMPLOYEE:
        return "No matching records were found in the records available to you."
    if role == Role.MANAGER:
        return "No matching records were found in your own or direct-report records."
    return "No matching records were found in the allowed HRMS data."