import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.core.config import settings
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
from app.services.ai import sql_agent
from app.services.ai.permissions import empty_result_message, guardrail_denial_message
from app.services.ai.sql_agent import _build_system_prompt
from app.services.ai.sql_guardrails import (
    SQLGuardrailError,
    clean_generated_sql,
    execute_scoped_select,
    get_scoped_employee_matches,
    get_scoped_schema,
)


class SQLGuardrailsTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "hrms.sqlite3"
        self.database_url = f"sqlite:///{self.database_path.as_posix()}"
        connection = sqlite3.connect(self.database_path)
        try:
            connection.executescript(
                """
                CREATE TABLE employees (id INTEGER PRIMARY KEY, manager_id INTEGER, name TEXT, hashed_password TEXT, current_salary_usd NUMERIC);
                CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE employee_projects (id INTEGER PRIMARY KEY, employee_id INTEGER, project_id INTEGER);
                CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE skills (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE employee_skills (id INTEGER PRIMARY KEY, employee_id INTEGER, skill_id INTEGER);
                CREATE TABLE job_history (id INTEGER PRIMARY KEY, employee_id INTEGER, designation TEXT);
                CREATE TABLE leave_balances (id INTEGER PRIMARY KEY, employee_id INTEGER, remaining REAL);
                CREATE TABLE leave_requests (id INTEGER PRIMARY KEY, employee_id INTEGER, status TEXT);
                CREATE TABLE tickets (id INTEGER PRIMARY KEY, employee_id INTEGER, assignee_id INTEGER, title TEXT);
                CREATE TABLE payroll_records (id INTEGER PRIMARY KEY, employee_id INTEGER, gross REAL, net REAL, pan TEXT, pf_uan TEXT, esi_no TEXT);
                INSERT INTO employees VALUES (1, NULL, 'Manager', 'secret', 100000), (2, 1, 'Report', 'secret', 80000), (3, NULL, 'Other', 'secret', 90000), (4, NULL, 'Employee User', 'secret', 70000), (5, NULL, 'Manager User', 'secret', 65000);
                INSERT INTO projects VALUES (1, 'Alpha');
                INSERT INTO employee_projects VALUES (1, 2, 1), (2, 3, 1);
                INSERT INTO departments VALUES (1, 'Engineering');
                INSERT INTO skills VALUES (1, 'Python');
                INSERT INTO employee_skills VALUES (1, 2, 1), (2, 3, 1);
                INSERT INTO job_history VALUES (1, 2, 'Engineer'), (2, 3, 'Analyst');
                INSERT INTO leave_balances VALUES (1, 2, 4), (2, 3, 9);
                INSERT INTO leave_requests VALUES (1, 2, 'PENDING'), (2, 3, 'APPROVED'), (3, 4, 'PENDING'), (4, 5, 'PENDING');
                INSERT INTO tickets VALUES (1, 2, 1, 'Report ticket'), (2, 3, NULL, 'Other ticket');
                INSERT INTO payroll_records VALUES (1, 2, 80000, 70000, 'PAN2', 'UAN2', 'ESI2'), (2, 3, 90000, 80000, 'PAN3', 'UAN3', 'ESI3'), (3, 1, 100000, 90000, 'PAN1', 'UAN1', 'ESI1');
                """
            )
        finally:
            connection.close()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_employee_views_are_limited_to_their_own_records(self):
        rows, _ = execute_scoped_select(
            self.database_url, 2, Role.EMPLOYEE, "SELECT id, name FROM employees"
        )
        self.assertEqual(rows, [{"id": 2, "name": "Report"}])

    def test_employee_aggregate_counts_only_scoped_records(self):
        rows, _ = execute_scoped_select(
            self.database_url, 2, Role.EMPLOYEE, "SELECT COUNT(*) AS total FROM employees"
        )
        self.assertEqual(rows, [{"total": 1}])

    def test_manager_views_include_direct_reports_only(self):
        rows, _ = execute_scoped_select(
            self.database_url, 1, Role.MANAGER, "SELECT id, name FROM employees ORDER BY id"
        )
        self.assertEqual([row["id"] for row in rows], [1, 2])

    def test_employee_name_lookup_matches_case_insensitively(self):
        rows, _ = execute_scoped_select(
            self.database_url,
            1,
            Role.ADMIN,
            "SELECT id, name FROM employees WHERE name = 'report'",
        )
        self.assertEqual(rows, [{"id": 2, "name": "Report"}])

    def test_partial_name_and_guessed_employee_id_resolve_to_full_scoped_name(self):
        message = "How many leave requests does employee user have?"
        matches = get_scoped_employee_matches(self.database_url, 1, Role.ADMIN, message)
        self.assertEqual(matches, [(4, "Employee User")])
        self.assertEqual(
            get_scoped_employee_matches(self.database_url, 1, Role.MANAGER, "Report"),
            [(2, "Report")],
        )
        self.assertEqual(
            get_scoped_employee_matches(self.database_url, 1, Role.MANAGER, "Employee User"),
            [],
        )

        partial_name_sql = clean_generated_sql(
            "SELECT id FROM employees WHERE name = 'User'",
            Role.ADMIN,
            matches,
        )
        name_rows, _ = execute_scoped_select(self.database_url, 1, Role.ADMIN, partial_name_sql)
        self.assertEqual(name_rows, [{"id": 4}])

        guessed_id_sql = clean_generated_sql(
            "SELECT COUNT(id) AS leave_request_count FROM leave_requests WHERE employee_id = 1",
            Role.ADMIN,
            matches,
        )
        count_rows, _ = execute_scoped_select(self.database_url, 1, Role.ADMIN, guessed_id_sql)
        self.assertEqual(count_rows, [{"leave_request_count": 1}])

    def test_pluralized_named_subject_replaces_inferred_manager_id_filter(self):
        message = "Tell me manager users pending leave requests"
        matches = get_scoped_employee_matches(self.database_url, 1, Role.ADMIN, message)
        self.assertEqual(matches, [(5, "Manager User")])
        subject_name = sql_agent._named_employee_subject_name(message, matches)
        self.assertEqual(subject_name, "Manager User")

        raw_sql = (
            "SELECT leave_requests.id, leave_requests.employee_id "
            "FROM leave_requests JOIN employees "
            "ON leave_requests.employee_id = employees.id "
            "WHERE leave_requests.status = 'PENDING' AND employees.manager_id = 1"
        )
        safe_sql = clean_generated_sql(raw_sql, Role.ADMIN, matches, subject_name)
        self.assertIn("employees.name COLLATE NOCASE = 'Manager User'", safe_sql)
        self.assertNotIn("employees.manager_id = 1", safe_sql)
        rows, _ = execute_scoped_select(self.database_url, 1, Role.ADMIN, safe_sql)
        self.assertEqual(rows, [{"id": 4, "employee_id": 5}])

    def test_named_reports_request_does_not_use_named_employee_as_subject(self):
        message = "Show pending leave requests for Manager User's direct reports"
        matches = [(5, "Manager User")]
        self.assertIsNone(sql_agent._named_employee_subject_name(message, matches))

    def test_scoped_schema_hides_sensitive_columns(self):
        schema = get_scoped_schema(self.database_url, 1, Role.ADMIN)
        self.assertIn("employees(id, manager_id, name)", schema)
        self.assertIn("payroll_records(id, employee_id, gross, net)", schema)
        self.assertNotIn("hashed_password", schema)
        for blocked_column in ("pan", "pf_uan", "esi_no", "bank_account_number"):
            self.assertNotIn(blocked_column, schema)

    def test_payroll_rows_follow_employee_manager_and_admin_scopes(self):
        query = "SELECT employee_id, gross, net FROM payroll_records ORDER BY employee_id"
        employee_rows, _ = execute_scoped_select(self.database_url, 2, Role.EMPLOYEE, query)
        manager_rows, _ = execute_scoped_select(self.database_url, 1, Role.MANAGER, query)
        admin_rows, _ = execute_scoped_select(self.database_url, 1, Role.ADMIN, query)
        self.assertEqual([row["employee_id"] for row in employee_rows], [2])
        self.assertEqual([row["employee_id"] for row in manager_rows], [1, 2])
        self.assertEqual([row["employee_id"] for row in admin_rows], [1, 2, 3])

    def test_current_salary_field_remains_blocked_for_all_roles(self):
        schema = get_scoped_schema(self.database_url, 1, Role.ADMIN)
        self.assertNotIn("current_salary_usd", schema)

    def test_write_and_multiple_statements_are_rejected(self):
        for sql in (
            "DELETE FROM employees",
            "SELECT 1; SELECT 2",
            "SELECT * FROM main.employees",
            "SELECT hashed_password FROM employees",
            "SELECT pan FROM payroll_records",
            "SELECT pf_uan FROM payroll_records",
            "SELECT esi_no FROM payroll_records",
            "SELECT current_salary_usd FROM employees",
            "SELECT id FROM employees WHERE EXISTS (SELECT 1 FROM other_table)",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLGuardrailError):
                clean_generated_sql(sql)

    def test_sqlglot_accepts_schema_aware_skill_joins(self):
        sql = """
        SELECT employees.name, skills.name, employee_skills.level
        FROM employees
        JOIN employee_skills ON employee_skills.employee_id = employees.id
        JOIN skills ON skills.id = employee_skills.skill_id
        WHERE skills.name LIKE '%Python%'
        """
        self.assertEqual(clean_generated_sql(sql, Role.MANAGER), sql.strip())

    def test_sql_prompt_includes_uppercase_project_status_values(self):
        prompt = _build_system_prompt(Role.ADMIN, "projects(id, status)")
        enum_types = (
            Role,
            EmployeeStatus,
            ProjectStatus,
            SkillLevel,
            LeaveType,
            LeaveRequestStatus,
            HalfDayPeriod,
            TicketCategory,
            TicketPriority,
            TicketStatus,
        )
        for enum_type in enum_types:
            for enum_value in enum_type:
                self.assertIn(enum_value.value, prompt)
        self.assertIn("do not lowercase them", prompt)
        self.assertIn("Use IS NULL or IS NOT NULL", prompt)
        self.assertIn("use 0 for false and 1 for true", prompt)

    def test_sql_prompt_describes_role_scoped_payroll_access(self):
        employee_prompt = _build_system_prompt(Role.EMPLOYEE, "payroll_records(employee_id, gross)")
        manager_prompt = _build_system_prompt(Role.MANAGER, "payroll_records(employee_id, gross)")
        admin_prompt = _build_system_prompt(Role.ADMIN, "payroll_records(employee_id, gross)")
        self.assertIn("Payroll access scope: self", employee_prompt)
        self.assertIn("Payroll access scope: team", manager_prompt)
        self.assertIn("Payroll access scope: all", admin_prompt)
        self.assertIn("case-insensitive employee name lookups", manager_prompt)
        self.assertIn("Never guess or hard-code an employee ID from a person's name", manager_prompt)
        self.assertIn("filter on employees.name case-insensitively", manager_prompt)

    def test_sql_prompt_receives_authenticated_employee_id(self):
        prompt = _build_system_prompt(Role.MANAGER, "employees(id)", employee_id=2)
        self.assertIn("Authenticated employee database ID: 2", prompt)
        self.assertIn("do not narrow those requests to the manager's own ID", prompt)

    def test_direct_database_access_cannot_bypass_scoped_view(self):
        for sql in (
            "SELECT id, name FROM main.employees",
            "SELECT COUNT(*) FROM main.employees",
        ):
            with self.subTest(sql=sql), self.assertRaises(SQLGuardrailError):
                execute_scoped_select(self.database_url, 2, Role.EMPLOYEE, sql)


class FakeOpenAIClient:
    def __init__(self, generated_sql: str):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=generated_sql))]
                    )
                )
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class SQLAgentResponseTests(unittest.TestCase):
    def run_agent(
        self,
        generated_sql: str,
        role: Role,
        result_rows=None,
        retry_sql: str | None = None,
        message: str = "Test question",
        named_employee_matches=None,
    ):
        client = FakeOpenAIClient(generated_sql)
        if retry_sql is not None:
            client.chat.completions.create.side_effect = [
                SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=generated_sql))]),
                SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=retry_sql))]),
            ]
        execute_query = patch.object(
            sql_agent,
            "execute_scoped_select",
            return_value=(result_rows or [], False),
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(sql_agent, "AsyncOpenAI", return_value=client),
            patch.object(sql_agent, "get_scoped_schema", return_value="employees(id, name)"),
            patch.object(
                sql_agent,
                "get_scoped_employee_matches",
                return_value=named_employee_matches or [],
            ),
            execute_query as mocked_execute,
        ):
            result = asyncio.run(
                sql_agent.answer_sql_question(
                    message,
                    [],
                    SimpleNamespace(id=17, role=role),
                    "sqlite:///unused.db",
                )
            )
        return result, client, mocked_execute

    def test_nonempty_query_uses_deterministic_row_count_without_summary_call(self):
        result, client, execute_query = self.run_agent(
            "SELECT id, name FROM employees", Role.ADMIN, result_rows=[{"id": 1, "name": "Admin"}]
        )
        self.assertEqual(result["answer"], "The query returned 1 matching result row.")
        self.assertEqual(result["rows"], [{"id": 1, "name": "Admin"}])
        self.assertFalse(result["truncated"])
        self.assertEqual(client.chat.completions.create.await_count, 1)
        execute_query.assert_called_once()

    def test_truncated_query_reports_more_than_the_visible_row_limit(self):
        result_rows = [{"id": row_id} for row_id in range(100)]
        client = FakeOpenAIClient("SELECT id FROM employees")
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(sql_agent, "AsyncOpenAI", return_value=client),
            patch.object(sql_agent, "get_scoped_schema", return_value="employees(id)"),
            patch.object(sql_agent, "get_scoped_employee_matches", return_value=[]),
            patch.object(sql_agent, "execute_scoped_select", return_value=(result_rows, True)),
        ):
            result = asyncio.run(
                sql_agent.answer_sql_question(
                    "List employees",
                    [],
                    SimpleNamespace(id=1, role=Role.ADMIN),
                    "sqlite:///unused.db",
                )
            )
        self.assertEqual(
            result["answer"],
            "More than 100 matching rows were found; showing the first 100.",
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["rows"]), 100)
        self.assertEqual(client.chat.completions.create.await_count, 1)

    def test_other_employee_denial_is_role_specific(self):
        employee_result, employee_client, employee_execute = self.run_agent(
            "DENIED:OTHER_EMPLOYEE_INFO", Role.EMPLOYEE
        )
        manager_result, manager_client, manager_execute = self.run_agent(
            "DENIED:OTHER_EMPLOYEE_INFO", Role.MANAGER
        )
        self.assertIn("another employee's information", employee_result["answer"])
        self.assertIn("outside your team", manager_result["answer"])
        self.assertTrue(employee_result["denied"])
        self.assertTrue(manager_result["denied"])
        employee_execute.assert_not_called()
        manager_execute.assert_not_called()
        self.assertEqual(employee_client.chat.completions.create.await_count, 1)
        self.assertEqual(manager_client.chat.completions.create.await_count, 2)

    def test_manager_direct_report_payroll_false_refusal_retries_into_scoped_query(self):
        payroll_sql = (
            "SELECT e.name, p.gross FROM employees e "
            "JOIN payroll_records p ON p.employee_id = e.id WHERE e.name = 'Jesse Spears'"
        )
        result, client, execute_query = self.run_agent(
            "DENIED:OTHER_EMPLOYEE_INFO",
            Role.MANAGER,
            result_rows=[{"name": "Jesse Spears", "gross": 95000}],
            retry_sql=payroll_sql,
        )
        self.assertEqual(result["rows"], [{"name": "Jesse Spears", "gross": 95000}])
        self.assertEqual(client.chat.completions.create.await_count, 2)
        execute_query.assert_called_once()

    def test_admin_other_employee_false_refusal_retries_into_scoped_query(self):
        result, client, execute_query = self.run_agent(
            "DENIED:OTHER_EMPLOYEE_INFO",
            Role.ADMIN,
            result_rows=[{"id": 42}],
            retry_sql="SELECT id FROM employees WHERE name = 'Jesse Spears'",
        )
        self.assertEqual(result["rows"], [{"id": 42}])
        self.assertFalse(result["denied"])
        self.assertEqual(client.chat.completions.create.await_count, 2)
        execute_query.assert_called_once()

    def test_admin_employee_id_question_uses_name_lookup_after_model_refuses_twice(self):
        result, client, execute_query = self.run_agent(
            "DENIED:OTHER_EMPLOYEE_INFO",
            Role.ADMIN,
            result_rows=[{"id": 3, "name": "Employee User"}],
            message="What is employee user id?",
            named_employee_matches=[(3, "Employee User")],
        )
        self.assertFalse(result["denied"])
        self.assertEqual(result["rows"], [{"id": 3, "name": "Employee User"}])
        self.assertEqual(client.chat.completions.create.await_count, 2)
        executed_sql = execute_query.call_args.args[3]
        self.assertIn("name COLLATE NOCASE = 'Employee User'", executed_sql)

    def test_manager_user_id_phrasings_use_scoped_name_lookup(self):
        for message in (
            "What is manager user id?",
            "What is manager users id?",
        ):
            with self.subTest(message=message):
                result, client, execute_query = self.run_agent(
                    "this is not safe SQL",
                    Role.MANAGER,
                    result_rows=[{"id": 5, "name": "Manager User"}],
                    message=message,
                    named_employee_matches=[(5, "Manager User")],
                )
                self.assertFalse(result["denied"])
                self.assertEqual(result["rows"], [{"id": 5, "name": "Manager User"}])
                self.assertEqual(client.chat.completions.create.await_count, 1)
                executed_sql = execute_query.call_args.args[3]
                self.assertIn("name COLLATE NOCASE = 'Manager User'", executed_sql)

    def test_manager_payroll_denial_retries_into_scoped_query(self):
        payroll_sql = (
            "SELECT e.name, p.gross FROM employees e "
            "JOIN payroll_records p ON p.employee_id = e.id WHERE e.name = 'Jesse Spears'"
        )
        result, client, execute_query = self.run_agent(
            "DENIED:PAYROLL_DATA",
            Role.MANAGER,
            result_rows=[{"name": "Jesse Spears", "gross": 95000}],
            retry_sql=payroll_sql,
        )
        self.assertEqual(result["rows"], [{"name": "Jesse Spears", "gross": 95000}])
        self.assertEqual(client.chat.completions.create.await_count, 2)
        execute_query.assert_called_once()

    def test_restricted_fields_are_denied_but_allowed_payroll_fields_are_accessible(self):
        restricted_result, _, restricted_execute = self.run_agent(
            "SELECT bank_account_number FROM employees", Role.ADMIN
        )
        payroll_result, _, payroll_execute = self.run_agent(
            "SELECT gross FROM payroll_records", Role.ADMIN, result_rows=[{"gross": 80000}]
        )
        self.assertEqual(
            restricted_result["answer"],
            "The requested fields cannot be accessed by the SQL assistant for any role.",
        )
        self.assertTrue(restricted_result["denied"])
        self.assertFalse(payroll_result["denied"])
        self.assertEqual(payroll_result["rows"], [{"gross": 80000}])
        restricted_execute.assert_not_called()
        payroll_execute.assert_called_once()

    def test_empty_authorized_query_returns_no_match_not_permission_denial(self):
        result, client, execute_query = self.run_agent(
            "SELECT id FROM employees WHERE id = 999", Role.EMPLOYEE
        )
        self.assertEqual(
            result["answer"],
            "No matching records were found in the records available to you.",
        )
        self.assertFalse(result["denied"])
        execute_query.assert_called_once()
        self.assertEqual(client.chat.completions.create.await_count, 1)

    def test_legacy_empty_sentinel_is_reported_as_out_of_scope(self):
        result, _, execute_query = self.run_agent("SELECT 1 WHERE 0", Role.EMPLOYEE)
        self.assertTrue(result["denied"])
        self.assertIn("do not have permission", result["answer"])
        execute_query.assert_not_called()


if __name__ == "__main__":
    unittest.main()