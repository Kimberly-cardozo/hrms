import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.core.config import settings
from app.models.enums import Role
from app.services.ai import action_agent, api_tools
from app.services.ai.action_agent import ActionAgentError


class FakeAssistantMessage:
    def __init__(self, tool_name: str, arguments: dict):
        self.content = None
        self.tool_calls = [
            SimpleNamespace(
                id="tool-call-1",
                function=SimpleNamespace(name=tool_name, arguments=json.dumps(arguments)),
            )
        ]

    def model_dump(self, exclude_none=True):
        return {"role": "assistant", "tool_calls": []}


class FakeOpenAIClient:
    def __init__(self, tool_name: str, arguments: dict):
        message = FakeAssistantMessage(tool_name, arguments)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=message)]))
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class ActionAgentTests(unittest.TestCase):
    def test_tool_allowlist_changes_by_role(self):
        employee_tools = {item["function"]["name"] for item in api_tools.get_action_tool_definitions(Role.EMPLOYEE)}
        manager_tools = {item["function"]["name"] for item in api_tools.get_action_tool_definitions(Role.MANAGER)}
        admin_tools = {item["function"]["name"] for item in api_tools.get_action_tool_definitions(Role.ADMIN)}

        self.assertIn("apply_leave", employee_tools)
        self.assertNotIn("approve_leave", employee_tools)
        self.assertNotIn("assign_ticket", employee_tools)
        self.assertNotIn("view_my_projects", employee_tools)
        self.assertNotIn("list_employee_projects", manager_tools)
        self.assertIn("handoff_query_assistant", employee_tools)
        self.assertIn("approve_leave", manager_tools)
        self.assertIn("assign_ticket", manager_tools)
        self.assertNotIn("create_project", manager_tools)
        self.assertIn("create_project", admin_tools)
        self.assertIn("upload_hr_policy", admin_tools)
        self.assertIn("summarize_hr_policy", admin_tools)

    def test_write_tool_only_proposes_until_user_confirms(self):
        client = FakeOpenAIClient(
            "create_ticket",
            {
                "title": "VPN access issue",
                "description": "VPN disconnects during work.",
                "category": "IT",
                "priority": "HIGH",
            },
        )
        user = SimpleNamespace(id=8, role=Role.EMPLOYEE)
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(action_agent, "AsyncOpenAI", return_value=client),
            patch.object(action_agent, "execute_action_tool", new=AsyncMock()) as execute,
        ):
            proposal = asyncio.run(action_agent.answer_action_question("Create a VPN ticket", [], user, "user-jwt"))

        self.assertEqual(proposal["action"]["status"], "confirmation_required")
        self.assertTrue(proposal["action"]["confirmation_token"])
        execute.assert_not_awaited()

    def test_confirmation_calls_only_the_selected_backend_api_tool_once(self):
        user = SimpleNamespace(id=8, role=Role.EMPLOYEE)
        arguments = {
            "title": "VPN access issue",
            "description": "VPN disconnects during work.",
            "category": "IT",
            "priority": "HIGH",
        }
        confirmation_token = action_agent._issue_confirmation_token(user, "create_ticket", arguments)
        call_api = AsyncMock(return_value={"id": 44, "status": "OPEN"})
        with patch.object(action_agent, "execute_action_tool", new=call_api):
            result = asyncio.run(action_agent.confirm_hr_action(user, "user-jwt", confirmation_token))
            self.assertEqual(result["action"]["status"], "completed")
            self.assertEqual(result["action"]["tool_name"], "create_ticket")
            call_api.assert_awaited_once_with(
                "create_ticket", arguments, "user-jwt", actor_id=8, role=Role.EMPLOYEE
            )

            with self.assertRaises(ActionAgentError) as error:
                asyncio.run(action_agent.confirm_hr_action(user, "user-jwt", confirmation_token))
            self.assertEqual(error.exception.code, "CONFIRMATION_ALREADY_USED")

    def test_tool_wrapper_uses_existing_api_route(self):
        api_request = AsyncMock(return_value={"items": []})
        with patch.object(api_tools, "_request_api", new=api_request):
            result = asyncio.run(api_tools.execute_action_tool("check_leave_balance", {}, "user-jwt"))
        self.assertEqual(result, {"items": []})
        api_request.assert_awaited_once_with("GET", "/api/v1/leaves/balances/me", "user-jwt")

    def test_manager_pending_leave_tool_filters_to_direct_reports(self):
        async def request_api(method, path, token, *, params=None, **kwargs):
            if path == "/api/v1/org/tree":
                return {
                    "items": [
                        {
                            "id": 1,
                            "manager_id": None,
                            "children": [
                                {"id": 2, "manager_id": 1, "children": []},
                                {"id": 3, "manager_id": 1, "children": []},
                            ],
                        }
                    ]
                }
            if path == "/api/v1/leaves/requests/pending":
                return {
                    "items": [
                        {"id": 11, "employee_id": 1, "status": "PENDING"},
                        {"id": 12, "employee_id": 2, "status": "PENDING"},
                        {"id": 13, "employee_id": 9, "status": "PENDING"},
                    ],
                    "meta": {"total": 3},
                }
            raise AssertionError(path)

        with patch.object(api_tools, "_request_api", new=AsyncMock(side_effect=request_api)):
            result = asyncio.run(
                api_tools.execute_action_tool(
                    "list_pending_leave_requests",
                    {},
                    "manager-jwt",
                    actor_id=1,
                    role=Role.MANAGER,
                )
            )
        self.assertEqual([item["id"] for item in result["items"]], [11, 12])
        self.assertEqual(result["meta"]["total"], 2)

    def test_manager_cannot_approve_leave_outside_team(self):
        async def request_api(method, path, token, *, params=None, **kwargs):
            if path == "/api/v1/org/tree":
                return {"items": [{"id": 1, "manager_id": None, "children": []}]}
            if path == "/api/v1/leaves/requests/pending":
                return {"items": [{"id": 22, "employee_id": 7, "status": "PENDING"}], "meta": {"total": 1}}
            raise AssertionError(f"Mutation/API call should not happen: {method} {path}")

        with patch.object(api_tools, "_request_api", new=AsyncMock(side_effect=request_api)):
            with self.assertRaises(api_tools.ActionToolError):
                asyncio.run(
                    api_tools.execute_action_tool(
                        "approve_leave",
                        {"request_id": 22},
                        "manager-jwt",
                        actor_id=1,
                        role=Role.MANAGER,
                    )
                )

    def test_policy_listing_does_not_send_internal_file_metadata_to_model(self):
        api_request = AsyncMock(
            return_value={
                "items": [
                    {
                        "id": 3,
                        "title": "Leave Policy",
                        "category": "LEAVE",
                        "original_filename": "leave.md",
                        "file_path": "/app/storage/secret-path",
                        "checksum": "private-checksum",
                        "uploaded_by": 1,
                    }
                ]
            }
        )
        with patch.object(api_tools, "_request_api", new=api_request):
            result = asyncio.run(api_tools.execute_action_tool("list_hr_policies", {}, "user-jwt"))
        self.assertEqual(
            result,
            {"items": [{"id": 3, "title": "Leave Policy", "category": "LEAVE", "original_filename": "leave.md"}]},
        )

    def test_actions_endpoint_requires_jwt(self):
        response = TestClient(app).post("/api/v1/chat/actions", json={"message": "Create a ticket"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
