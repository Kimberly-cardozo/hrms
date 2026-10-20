import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.services.ai import policy_rag


class FakeOpenAIClient:
    def __init__(self, answer_json: str):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=answer_json))]
                    )
                )
            )
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None


class PolicyRagTests(unittest.TestCase):
    def test_ingestion_scope_is_exactly_the_three_approved_sources(self):
        self.assertEqual(
            set(policy_rag.POLICY_DOCUMENTS),
            {"leave_policy_sample.pdf", "policy_sample.pdf", "privacy_policy_sample.txt"},
        )

    def test_chunks_preserve_document_page_and_citation_id(self):
        chunks = policy_rag.chunk_policy_pages(
            [{"filename": "leave_policy_sample.pdf", "title": "Leave Policy", "page": 2, "text": "Leave rules " * 250}]
        )
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(chunk["filename"] == "leave_policy_sample.pdf" for chunk in chunks))
        self.assertTrue(all(chunk["page"] == 2 for chunk in chunks))
        self.assertTrue(all(chunk["chunk_id"] for chunk in chunks))

    def test_sentence_and_item_chunking_attaches_topic_and_separates_rules(self):
        policy_text = (
            "Leave Policy\n"
            "1) Casual leave: 12 days per year.\n"
            "2) Sick leave: 10 days per year.\n"
            "3) Earned leave: 15 days per year.\n"
        )
        chunks = policy_rag.chunk_policy_pages(
            [{"filename": "leave_policy_sample.pdf", "title": "Leave Policy Sample", "page": 1, "text": policy_text}]
        )
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["topic"], "Leave Policy")
        self.assertEqual(chunks[0]["section"], "Casual leave")
        self.assertEqual(chunks[0]["text"], "1) Casual leave: 12 days per year.")
        self.assertEqual(chunks[0]["embed_text"], "[Leave Policy] 1) Casual leave: 12 days per year.")
        self.assertEqual(chunks[1]["section"], "Sick leave")
        self.assertEqual(chunks[1]["text"], "2) Sick leave: 10 days per year.")

    def test_supported_answer_requires_a_citation_from_retrieved_context(self):
        chunk = {
            "chunk_id": "chunk-leave-1",
            "filename": "leave_policy_sample.pdf",
            "title": "Leave Policy Sample",
            "page": 2,
            "section": "Sick leave",
            "text": "Employees can request sick leave using the HRMS.",
            "embedding": [1.0, 0.0],
        }
        fake_client = FakeOpenAIClient(
            json.dumps({"supported": True, "answer": "Submit sick leave through the HRMS.", "source_ids": ["chunk-leave-1"]})
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(policy_rag, "_get_policy_index", new=AsyncMock(return_value={"chunks": [chunk]})),
            patch.object(policy_rag, "embed_query", new=AsyncMock(return_value=[1.0, 0.0])),
            patch.object(policy_rag, "AsyncOpenAI", return_value=fake_client),
        ):
            result = asyncio.run(policy_rag.answer_policy_question("How do I request sick leave?"))

        self.assertFalse(result["refused"])
        self.assertEqual(result["answer"], "Submit sick leave through the HRMS.")
        self.assertEqual(result["sources"][0]["filename"], "leave_policy_sample.pdf")
        self.assertEqual(result["sources"][0]["page"], 2)

    def test_uncited_model_answer_is_replaced_with_out_of_scope_refusal(self):
        chunk = {
            "chunk_id": "chunk-privacy-1",
            "filename": "privacy_policy_sample.txt",
            "title": "Privacy Policy Sample",
            "page": None,
            "section": "Privacy",
            "text": "Employee records are restricted.",
            "embedding": [1.0],
        }
        fake_client = FakeOpenAIClient(
            json.dumps({"supported": True, "answer": "Unsupported claim.", "source_ids": ["invented-source"]})
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(policy_rag, "_get_policy_index", new=AsyncMock(return_value={"chunks": [chunk]})),
            patch.object(policy_rag, "embed_query", new=AsyncMock(return_value=[1.0])),
            patch.object(policy_rag, "AsyncOpenAI", return_value=fake_client),
        ):
            result = asyncio.run(policy_rag.answer_policy_question("What is the privacy policy?"))

        self.assertTrue(result["refused"])
        self.assertEqual(result["sources"], [])
        self.assertIn("beyond the scope", result["answer"])

    def test_low_similarity_refuses_without_calling_answer_model(self):
        chunk = {
            "chunk_id": "chunk-1",
            "filename": "leave_policy_sample.pdf",
            "title": "Leave Policy Sample",
            "page": 1,
            "section": "Leave",
            "text": "Leave policy text.",
            "embedding": [1.0, 0.0],
        }
        answer_client = FakeOpenAIClient("{}")
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(settings, "policy_rag_min_similarity", 0.5),
            patch.object(policy_rag, "_get_policy_index", new=AsyncMock(return_value={"chunks": [chunk]})),
            patch.object(policy_rag, "embed_query", new=AsyncMock(return_value=[0.0, 1.0])),
            patch.object(policy_rag, "AsyncOpenAI", return_value=answer_client),
        ):
            result = asyncio.run(policy_rag.answer_policy_question("Write Python code"))

        self.assertTrue(result["refused"])
        self.assertEqual(answer_client.chat.completions.create.await_count, 0)
        self.assertEqual(result["sources"], [])

    def test_policy_endpoint_requires_jwt(self):
        response = TestClient(app).post("/api/v1/chat/policy", json={"message": "What is the leave policy?"})
        self.assertEqual(response.status_code, 401)

    def test_translate_request_refuses_that_it_can_only_speak_english(self):
        result = asyncio.run(policy_rag.answer_policy_question("Translate the leave policy to Spanish"))
        self.assertTrue(result["refused"])
        self.assertEqual(result["answer"], "I can only speak English.")
        self.assertEqual(result["sources"], [])

    def test_non_english_question_refuses_that_it_can_only_speak_english(self):
        result = asyncio.run(policy_rag.answer_policy_question("¿Cuántos días de vacaciones tengo?"))
        self.assertTrue(result["refused"])
        self.assertEqual(result["answer"], "I can only speak English.")
        self.assertEqual(result["sources"], [])

    def test_model_reporting_english_only_refusal_is_honored(self):
        chunk = {
            "chunk_id": "chunk-leave-1",
            "filename": "leave_policy_sample.pdf",
            "title": "Leave Policy Sample",
            "page": 1,
            "section": "Leave",
            "text": "Leave rules.",
            "embedding": [1.0, 0.0],
        }
        fake_client = FakeOpenAIClient(
            json.dumps({"supported": False, "answer": "I can only speak English.", "source_ids": []})
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(policy_rag, "_get_policy_index", new=AsyncMock(return_value={"chunks": [chunk]})),
            patch.object(policy_rag, "embed_query", new=AsyncMock(return_value=[1.0, 0.0])),
            patch.object(policy_rag, "AsyncOpenAI", return_value=fake_client),
        ):
            result = asyncio.run(policy_rag.answer_policy_question("Explain this policy"))

        self.assertTrue(result["refused"])
        self.assertEqual(result["answer"], "I can only speak English.")
        self.assertEqual(result["sources"], [])

    def test_model_generating_non_english_answer_is_intercepted(self):
        chunk = {
            "chunk_id": "chunk-leave-1",
            "filename": "leave_policy_sample.pdf",
            "title": "Leave Policy Sample",
            "page": 1,
            "section": "Leave",
            "text": "Leave rules.",
            "embedding": [1.0, 0.0],
        }
        fake_client = FakeOpenAIClient(
            json.dumps({
                "supported": True,
                "answer": "Los empleados pueden solicitar permisos por enfermedad con la aprobación de su gerente.",
                "source_ids": ["chunk-leave-1"],
            })
        )
        with (
            patch.object(settings, "openai_api_key", "test-key"),
            patch.object(policy_rag, "_get_policy_index", new=AsyncMock(return_value={"chunks": [chunk]})),
            patch.object(policy_rag, "embed_query", new=AsyncMock(return_value=[1.0, 0.0])),
            patch.object(policy_rag, "AsyncOpenAI", return_value=fake_client),
        ):
            result = asyncio.run(policy_rag.answer_policy_question("What is the leave policy?"))

        self.assertTrue(result["refused"])
        self.assertEqual(result["answer"], "I can only speak English.")
        self.assertEqual(result["sources"], [])


if __name__ == "__main__":
    unittest.main()
