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
                CREATE TABLE employees (id INTEGER PRIMARY KEY, manager_id INTEGER, name TEXT, hashed_password TEXT);
                CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE employee_projects (id INTEGER PRIMARY KEY, employee_id INTEGER, project_id INTEGER);
                CREATE TABLE departments (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE skills (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE employee_skills (id INTEGER PRIMARY KEY, employee_id INTEGER, skill_id INTEGER);
                CREATE TABLE job_history (id INTEGER PRIMARY KEY, employee_id INTEGER, designation TEXT);
                CREATE TABLE leave_balances (id INTEGER PRIMARY KEY, employee_id INTEGER, remaining REAL);
                CREATE TABLE leave_requests (id INTEGER PRIMARY KEY, employee_id INTEGER, status TEXT);
                CREATE TABLE tickets (id INTEGER PRIMARY KEY, employee_id INTEGER, assignee_id INTEGER, title TEXT);
                INSERT INTO employees VALUES (1, NULL, 'Manager', 'secret'), (2, 1, 'Report', 'secret'), (3, NULL, 'Other', 'secret');
                INSERT INTO projects VALUES (1, 'Alpha');
                INSERT INTO employee_projects VALUES (1, 2, 1), (2, 3, 1);
                INSERT INTO departments VALUES (1, 'Engineering');
                INSERT INTO skills VALUES (1, 'Python');
                INSERT INTO employee_skills VALUES (1, 2, 1), (2, 3, 1);
                INSERT INTO job_history VALUES (1, 2, 'Engineer'), (2, 3, 'Analyst');
                INSERT INTO leave_balances VALUES (1, 2, 4), (2, 3, 9);
                INSERT INTO leave_requests VALUES (1, 2, 'PENDING'), (2, 3, 'APPROVED');
                INSERT INTO tickets VALUES (1, 2, 1, 'Report ticket'), (2, 3, NULL, 'Other ticket');
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

    def test_scoped_schema_hides_sensitive_columns(self):
        schema = get_scoped_schema(self.database_url, 1, Role.ADMIN)
        self.assertIn("employees(id, manager_id, name)", schema)
        self.assertNotIn("hashed_password", schema)

    def test_write_and_multiple_statements_are_rejected(self):
        for sql in (
            "DELETE FROM employees",
            "SELECT 1; SELECT 2",
            "SELECT id FROM payroll_records",
            "SELECT * FROM main.employees",
            "SELECT hashed_password FROM employees",
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
    def run_agent(self, generated_sql: str, role: Role, result_rows=None):
        client = FakeOpenAIClient(generated_sql)
        execute_query = patch.object(
            sql_agent,
            "execute_scoped_select",
            return_value=(result_rows or [], False),
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(sql_agent, "AsyncOpenAI", return_value=client),
            patch.object(sql_agent, "get_scoped_schema", return_value="employees(id, name)"),
            execute_query as mocked_execute,
        ):
            result = asyncio.run(
                sql_agent.answer_sql_question(
                    "Test question",
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
        self.assertEqual(manager_client.chat.completions.create.await_count, 1)

    def test_restricted_fields_and_payroll_receive_explicit_denials(self):
        restricted_result, _, restricted_execute = self.run_agent(
            "SELECT bank_account_number FROM employees", Role.ADMIN
        )
        payroll_result, _, payroll_execute = self.run_agent(
            "SELECT gross FROM payroll_records", Role.ADMIN
        )
        self.assertEqual(
            restricted_result["answer"],
            "The requested fields cannot be accessed by the SQL assistant for any role.",
        )
        self.assertEqual(
            payroll_result["answer"],
            "The SQL assistant does not have access to payroll data for any role.",
        )
        self.assertTrue(restricted_result["denied"])
        self.assertTrue(payroll_result["denied"])
        restricted_execute.assert_not_called()
        payroll_execute.assert_not_called()

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