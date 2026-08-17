"""Focused tests for task scheduling and the local task-runner pipeline."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.ai.prompts.local_llm.local_llm_task_runner import (
    SYSTEM_PROMPT as TASK_RUNNER_SYSTEM_PROMPT,
)
from src.client.tools import build_llama_tools, tool_names_for
from src.client.tools.registry import validate_tool_call
from src.db import ConversationSource, MessageRole, TaskModel
from src.services.local_llm import LocalLLMService
from src.services.local_tool_history import (
    deserialize_tool_trace,
    serialize_tool_trace,
    tool_trace_messages,
)
from src.services.task_scheduler import TaskScheduler
from src.services.task_service import _local_iso


class LocalTaskRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scheduler = TaskScheduler()

    def tearDown(self) -> None:
        self.scheduler._executor.shutdown(wait=True)

    def test_runner_cannot_manage_tasks(self) -> None:
        self.assertEqual(
            tool_names_for("local_task_runner"),
            ["messages", "memories", "current_time"],
        )

    def test_runner_prompt_describes_every_available_tool(self) -> None:
        for tool_name in ("messages", "memories", "current_time"):
            self.assertIn(f"{tool_name} -", TASK_RUNNER_SYSTEM_PROMPT)
        self.assertNotIn("tasks - schedule", TASK_RUNNER_SYSTEM_PROMPT)

    def test_runner_uses_dedicated_prompt_and_restricted_tools(self) -> None:
        captured = {}

        class FakeLLM:
            def generate(self, **kwargs):
                captured.update(kwargs)
                return "Finished the requested work."

        self.scheduler._llm = FakeLLM()
        self.scheduler._embeddings = object()
        task = SimpleNamespace(id=7, model=TaskModel.LOCAL, prompt="Do the thing now")

        status, result = self.scheduler._run_agent(task)

        self.assertEqual((status, result), ("completed", "Finished the requested work."))
        self.assertEqual(captured["system_prompt"], TASK_RUNNER_SYSTEM_PROMPT)
        self.assertEqual(
            [tool["function"]["name"] for tool in captured["tools"]],
            ["messages", "memories", "current_time"],
        )

    @patch("src.services.task_scheduler.create_message")
    @patch("src.services.task_scheduler.create_conversation")
    def test_task_run_starts_a_normal_local_chat(
        self,
        create_conversation: Mock,
        create_message: Mock,
    ) -> None:
        create_conversation.return_value = SimpleNamespace(id=42)
        task = SimpleNamespace(id=7, title="Morning check", prompt="Check in with me")

        conversation_id = self.scheduler._start_local_task_conversation(task)

        self.assertEqual(conversation_id, 42)
        self.assertEqual(create_conversation.call_args.kwargs["source"], ConversationSource.LOCAL)
        self.assertEqual(
            create_conversation.call_args.kwargs["metadata"]["pipeline"],
            "task_runner",
        )
        create_message.assert_called_once_with(
            42,
            role=MessageRole.USER,
            content="Check in with me",
        )

    @patch("src.services.task_scheduler.end_conversation")
    @patch("src.services.task_scheduler.create_message")
    def test_task_run_finishes_the_local_chat(
        self,
        create_message: Mock,
        end_conversation: Mock,
    ) -> None:
        self.scheduler._finish_local_task_conversation(42, "completed", "Hello!")

        create_message.assert_called_once_with(
            42,
            role=MessageRole.MODEL,
            content="Hello!",
        )
        end_conversation.assert_called_once_with(42)


class TaskTimeDisplayTests(unittest.TestCase):
    def test_naive_stored_utc_is_rendered_as_same_instant_with_offset(self) -> None:
        stored_utc = datetime(2026, 8, 17, 18, 23, 25)

        displayed = datetime.fromisoformat(
            _local_iso(stored_utc, stored_as_utc=True)
        )

        self.assertEqual(
            displayed.astimezone(timezone.utc),
            stored_utc.replace(tzinfo=timezone.utc),
        )

    def test_interactive_and_runner_tool_policies_are_distinct(self) -> None:
        interactive = {
            tool["function"]["name"] for tool in build_llama_tools("local_model")
        }
        runner = {
            tool["function"]["name"]
            for tool in build_llama_tools("local_task_runner")
        }

        self.assertIn("tasks", interactive)
        self.assertNotIn("tasks", runner)
        self.assertEqual(runner, {"messages", "memories", "current_time"})


class TaskToolReliabilityTests(unittest.TestCase):
    def test_local_service_adds_a_fresh_runtime_clock_to_the_system_prompt(self) -> None:
        captured = {}
        service = LocalLLMService()

        def fake_worker(messages, _temperature, _token, on_complete, *_args):
            captured["system_prompt"] = messages[0]["content"]
            on_complete("done")

        service._worker_stream = fake_worker

        result = service.generate(
            messages=[{"role": "user", "content": "Hello"}],
            system_prompt="Base prompt",
        )

        self.assertEqual(result, "done")
        self.assertTrue(captured["system_prompt"].startswith("Base prompt"))
        self.assertIn("## Runtime clock", captured["system_prompt"])
        self.assertIn("Current local time:", captured["system_prompt"])

    def test_add_validation_lists_every_missing_parameter(self) -> None:
        result = validate_tool_call(
            "tasks",
            {"action": "add", "schedule_type": "one_shot"},
        )

        self.assertEqual(result["status"], "validation_error")
        self.assertEqual(
            result["missing_parameters"],
            ["title", "prompt", "model", "run_at"],
        )
        self.assertIn("Do not tell the user it succeeded", result["message"])

    def test_add_validation_rejects_wrong_reminder_perspective(self) -> None:
        result = validate_tool_call(
            "tasks",
            {
                "action": "add",
                "title": "Call dad",
                "prompt": "Call your dad",
                "model": "local",
                "schedule_type": "one_shot",
                "run_at": "2026-08-17T12:05:11-07:00",
            },
        )

        self.assertEqual(result["status"], "validation_error")
        self.assertEqual(result["invalid_parameters"], ["prompt"])
        self.assertIn("Remind the user to call their dad", result["message"])

    def test_tool_trace_round_trip_rebuilds_model_context(self) -> None:
        content = serialize_tool_trace(
            "tasks",
            {"action": "add"},
            {
                "status": "validation_error",
                "missing_parameters": ["title"],
            },
        )

        self.assertEqual(deserialize_tool_trace(content)["name"], "tasks")
        messages = tool_trace_messages(content)
        self.assertEqual([message["role"] for message in messages], ["assistant", "user"])
        self.assertIn("<function=tasks>", messages[0]["content"])
        self.assertIn("validation_error", messages[1]["content"])

    @patch("src.services.local_llm.dispatch_tool_call")
    def test_false_scheduling_claim_is_retried_and_success_is_grounded(
        self,
        dispatch_tool_call: Mock,
    ) -> None:
        dispatch_tool_call.return_value = {
            "status": "created",
            "task_id": 9,
            "next_run_at": "2026-08-17T12:05:11-07:00",
        }
        service = LocalLLMService()
        service._llm = object()
        service._generate_round = Mock(
            side_effect=[
                ([], "Your reminder has been scheduled."),
                (
                    [
                        {
                            "id": "call_1",
                            "name": "tasks",
                            "args": {
                                "action": "add",
                                "title": "Call dad",
                                "prompt": "Remind the user to call their dad",
                                "model": "local",
                                "schedule_type": "one_shot",
                                "run_at": "2026-08-17T12:05:11-07:00",
                            },
                        }
                    ],
                    "",
                ),
            ]
        )

        result = service.generate(
            messages=[{"role": "user", "content": "Remind me to call dad"}],
            tools=build_llama_tools("local_model"),
            tool_context=SimpleNamespace(),
        )

        self.assertEqual(
            result,
            "Task #9 was scheduled successfully for 2026-08-17T12:05:11-07:00.",
        )
        self.assertEqual(service._generate_round.call_count, 2)
