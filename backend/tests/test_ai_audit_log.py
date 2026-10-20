import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.ai_audit_log import AIAuditLog
from app.models.employee import Employee
from app.models.enums import EmployeeStatus, Role
from app.services.ai import audit
from app.services.auth import get_current_user, oauth2_scheme


class AIAuditLogTests(unittest.TestCase):
    def test_model_fields_and_properties(self):
        log = AIAuditLog(
            user_id=1,
            role="EMPLOYEE",
            message="Check vacation leave",
            intent="POLICY_RAG",
            tool_or_api_used="/api/v1/chat/policy",
            action_status="COMPLETED",
        )
        self.assertEqual(log.user_id, 1)
        self.assertEqual(log.role, "EMPLOYEE")
        self.assertEqual(log.message, "Check vacation leave")
        self.assertEqual(log.intent, "POLICY_RAG")
        self.assertEqual(log.tool_or_api_used, "/api/v1/chat/policy")
        self.assertEqual(log.tool_used, "/api/v1/chat/policy")
        self.assertEqual(log.api_used, "/api/v1/chat/policy")
        self.assertEqual(log.action_status, "COMPLETED")
        self.assertEqual(log.status, "COMPLETED")

    def test_log_ai_interaction_persists_and_queries(self):
        async def run_test():
            logged = await audit.log_ai_interaction(
                user_id=1,
                role=Role.EMPLOYEE,
                message="How many casual leave days do I have?",
                intent="POLICY_RAG",
                tool_or_api_used="/api/v1/chat/policy",
                action_status="COMPLETED",
            )
            self.assertIsNotNone(logged)
            self.assertIsNotNone(logged.id)
            self.assertEqual(logged.role, "EMPLOYEE")
            self.assertEqual(logged.intent, "POLICY_RAG")
            self.assertEqual(logged.action_status, "COMPLETED")

            from app.db.session import SessionLocal
            async with SessionLocal() as db:
                records = await audit.get_ai_audit_logs(
                    db,
                    user_id=1,
                    intent="POLICY_RAG",
                    limit=10,
                )
                self.assertTrue(any(item.id == logged.id for item in records))

        asyncio.run(run_test())

    def test_policy_endpoint_audit_logging(self):
        mock_user = Employee(
            id=2,
            name="Test User",
            email="test@mock-hrms.dev",
            role=Role.EMPLOYEE,
            status=EmployeeStatus.ACTIVE,
        )

        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            with patch("app.api.v1.endpoints.chat.answer_policy_question", new=AsyncMock(return_value={
                "answer": "12 days casual leave per year.",
                "sources": [],
                "refused": False,
            })):
                client = TestClient(app)
                response = client.post("/api/v1/chat/policy", json={"message": "What is casual leave?"})
                self.assertEqual(response.status_code, 200)

            from app.db.session import SessionLocal
            async def verify_log():
                async with SessionLocal() as db:
                    logs = await audit.get_ai_audit_logs(db, user_id=2, intent="POLICY_RAG", limit=5)
                    self.assertTrue(any(l.message == "What is casual leave?" and l.action_status == "COMPLETED" for l in logs))

            asyncio.run(verify_log())
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_sql_endpoint_audit_logging(self):
        mock_user = Employee(
            id=3,
            name="Manager User",
            email="manager@mock-hrms.dev",
            role=Role.MANAGER,
            status=EmployeeStatus.ACTIVE,
        )

        app.dependency_overrides[get_current_user] = lambda: mock_user
        try:
            with patch("app.api.v1.endpoints.chat.answer_sql_question", new=AsyncMock(return_value={
                "answer": "The query returned 1 matching result row.",
                "sql": "SELECT id FROM employees WHERE id = 3",
                "rows": [{"id": 3}],
                "truncated": False,
                "denied": False,
            })):
                client = TestClient(app)
                response = client.post("/api/v1/chat/sql", json={"message": "Show my employee id"})
                self.assertEqual(response.status_code, 200)

            from app.db.session import SessionLocal
            async def verify_log():
                async with SessionLocal() as db:
                    logs = await audit.get_ai_audit_logs(db, user_id=3, intent="SQL_QUERY", limit=5)
                    self.assertTrue(any(l.message == "Show my employee id" and l.action_status == "COMPLETED" for l in logs))

            asyncio.run(verify_log())
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_action_endpoint_audit_logging(self):
        mock_user = Employee(
            id=4,
            name="Employee User",
            email="emp@mock-hrms.dev",
            role=Role.EMPLOYEE,
            status=EmployeeStatus.ACTIVE,
        )

        app.dependency_overrides[get_current_user] = lambda: mock_user
        app.dependency_overrides[oauth2_scheme] = lambda: "valid-jwt"
        try:
            with patch("app.api.v1.endpoints.chat.answer_action_question", new=AsyncMock(return_value={
                "answer": "Please review and confirm this action before it is submitted.",
                "action": {
                    "status": "confirmation_required",
                    "tool_name": "create_ticket",
                    "label": "Create Ticket",
                    "summary": "Create ticket: VPN setup",
                    "confirmation_token": "mock-token-1234567890",
                },
            })):
                client = TestClient(app)
                response = client.post("/api/v1/chat/actions", json={"message": "Create ticket for VPN"})
                self.assertEqual(response.status_code, 200)

            from app.db.session import SessionLocal
            async def verify_log():
                async with SessionLocal() as db:
                    logs = await audit.get_ai_audit_logs(db, user_id=4, intent="HR_TASK_AUTOMATION", limit=5)
                    self.assertTrue(any(l.tool_or_api_used == "create_ticket" and l.action_status == "confirmation_required" for l in logs))

            asyncio.run(verify_log())
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            app.dependency_overrides.pop(oauth2_scheme, None)

    def test_get_audit_logs_endpoint(self):
        admin_user = Employee(
            id=1,
            name="Admin User",
            email="admin@mock-hrms.dev",
            role=Role.ADMIN,
            status=EmployeeStatus.ACTIVE,
        )

        app.dependency_overrides[get_current_user] = lambda: admin_user
        try:
            client = TestClient(app)
            response = client.get("/api/v1/chat/audit-logs?limit=5")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["success"])
            self.assertIsInstance(data["data"], list)
            if data["data"]:
                item = data["data"][0]
                self.assertIn("user_id", item)
                self.assertIn("role", item)
                self.assertIn("message", item)
                self.assertIn("intent", item)
                self.assertIn("tool_or_api_used", item)
                self.assertIn("action_status", item)
                self.assertIn("timestamp", item)
        finally:
            app.dependency_overrides.pop(get_current_user, None)


if __name__ == "__main__":
    unittest.main()
