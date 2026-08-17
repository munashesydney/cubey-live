"""
Local LLM Service module.
Handles local Qwen3.5 2B inference via native llama-cpp-python running in memory.
Uses huggingface_hub to auto-download GGUF models.
"""

import json
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional

from src.ai.prompts.local_llm import SYSTEM_PROMPT as _DEFAULT_SYSTEM_PROMPT
from src.ai.prompts.local_llm.shared_tools import build_current_time_context
from src.client.tools.registry import ToolContext, dispatch_tool_call
from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Qwen's text-based function calling format (this GGUF emits tool calls as
# text, not via the structured channel).
_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
_TOOL_CALL_FUNC_RE = re.compile(r"<function=([^>]+)>")
_TOOL_CALL_PARAM_RE = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.DOTALL)
_TASK_REQUEST_RE = re.compile(
    r"\b(?:remind\s+me|schedule|set\s+(?:up\s+)?(?:a\s+)?reminder|create\s+(?:a\s+)?task)\b",
    re.IGNORECASE,
)
_TASK_SUCCESS_CLAIM_RE = re.compile(
    r"\b(?:task|reminder)\b.{0,60}\b(?:scheduled|created|set)\b|"
    r"\b(?:scheduled|created|set)\b.{0,60}\b(?:task|reminder)\b",
    re.IGNORECASE | re.DOTALL,
)


def _latest_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages):
        content = str(message.get("content") or "")
        if message.get("role") == "user" and not content.startswith("<tool_response>"):
            return content
    return ""


def _task_created_confirmation(result: dict[str, Any]) -> Optional[str]:
    """Build a factual confirmation directly from a successful tool result."""

    if result.get("status") != "created":
        return None
    return (
        f"Task #{result.get('task_id')} was scheduled successfully for "
        f"{result.get('next_run_at')}."
    )


def _parse_qwen_tool_calls(text: str) -> List[dict]:
    """Parse Qwen text-format tool calls like:
    <tool_call><function=messages><parameter=query>user name</parameter></function></tool_call>
    Returns [{name, args}]. Values are kept as strings (executors coerce)."""
    calls: List[dict] = []
    for block in _TOOL_CALL_BLOCK_RE.findall(text):
        func_match = _TOOL_CALL_FUNC_RE.search(block)
        if not func_match:
            continue
        args: Dict[str, str] = {}
        for param in _TOOL_CALL_PARAM_RE.finditer(block):
            args[param.group(1).strip()] = param.group(2).strip()
        calls.append({"name": func_match.group(1).strip(), "args": args})
    return calls


def _strip_tool_blocks(text: str) -> str:
    """Remove Qwen text-format tool-call blocks (for display/persistence)."""
    return _TOOL_CALL_BLOCK_RE.sub("", text).strip()


class LocalLLMService:
    """Service wrapper for interacting with native llama.cpp in-memory engine."""

    def __init__(
        self,
        repo_id: str = "bartowski/Qwen_Qwen3.5-2B-GGUF",
        filename: str = "Qwen_Qwen3.5-2B-Q4_K_M.gguf",
        n_ctx: int = 4096,
        default_system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
    ):
        self.repo_id = repo_id
        self.filename = filename
        self.n_ctx = n_ctx
        self.default_system_prompt = default_system_prompt

        self._llm: Any = None  # Lazy loaded Llama instance
        self._model_path: Optional[str] = None
        self._is_loading = False

        # Guards the one-time model download/load against concurrent callers.
        self._load_lock = threading.Lock()
        # Serializes llama.cpp inference (not safe for concurrent calls).
        self._llm_lock = threading.Lock()

        self._active_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def check_health(self) -> bool:
        """Return True if model is loaded into memory or is currently downloading."""
        return self._llm is not None or self._is_loading

    def is_generating(self) -> bool:
        """Return True if background generation thread is active."""
        return self._active_thread is not None and self._active_thread.is_alive()

    def stop_generation(self) -> None:
        """Signal active generation stream to halt."""
        self._stop_event.set()

    @staticmethod
    def _with_runtime_context(system_prompt: str) -> str:
        """Attach a fresh clock snapshot without changing Gemini's prompt path."""

        return f"{system_prompt}\n\n{build_current_time_context()}"

    def _ensure_model_loaded(self) -> None:
        """Blocking call to download and load the model into memory. Runs on worker thread."""
        if self._llm is not None:
            return

        with self._load_lock:
            if self._llm is not None:  # re-check under the lock
                return

            self._is_loading = True
            try:
                logger.info("Checking for local GGUF model: %s / %s", self.repo_id, self.filename)

                # This will use cached file if it exists, otherwise downloads it
                from huggingface_hub import hf_hub_download
                import llama_cpp

                model_dir = PROJECT_ROOT / "data" / "models"
                model_dir.mkdir(parents=True, exist_ok=True)

                self._model_path = hf_hub_download(
                    repo_id=self.repo_id,
                    filename=self.filename,
                    local_dir=str(model_dir),
                )

                logger.info("Loading native llama.cpp model from %s", self._model_path)
                self._llm = llama_cpp.Llama(
                    model_path=self._model_path,
                    n_ctx=self.n_ctx,
                    n_threads=max(1, os.cpu_count() or 4),
                    verbose=False,
                )
                logger.info("Local llama.cpp model loaded successfully.")
            except ImportError:
                raise ImportError(
                    "llama-cpp-python or huggingface-hub is missing. "
                    "Install via: pip install llama-cpp-python huggingface-hub"
                ) from None
            except Exception as e:
                logger.error("Failed to initialize local llama.cpp model: %s", e)
                raise
            finally:
                self._is_loading = False

    def generate(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        tools: Optional[List[dict]] = None,
        tool_context: Optional[ToolContext] = None,
        on_tool_call: Optional[Callable[[str, dict, dict], None]] = None,
        max_tool_rounds: int = 8,
    ) -> str:
        """
        Synchronous generation for headless task runs (blocks the caller).

        Runs the same agentic loop as streaming, collecting the final text.
        Raises RuntimeError if generation fails.
        """
        self._stop_event.clear()
        result: dict = {}

        def on_complete(text: str) -> None:
            result["text"] = text

        def on_error(err: str) -> None:
            result["error"] = err

        sys_prompt = self._with_runtime_context(
            system_prompt if system_prompt is not None else self.default_system_prompt
        )
        payload: List[Dict[str, Any]] = []
        if sys_prompt:
            payload.append({"role": "system", "content": sys_prompt})
        payload.extend(messages)

        # _worker_stream is synchronous and invokes on_complete/on_error
        # before returning, so no event is needed.
        self._worker_stream(
            payload, temperature, None, on_complete, on_error, on_tool_call,
            tools, tool_context, max_tool_rounds,
        )
        if "error" in result:
            raise RuntimeError(result["error"])
        return result.get("text", "")

    def stream_chat_completion(
        self,
        messages: List[Dict[str, str]],
        system_prompt: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        on_complete: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_tool_call: Optional[Callable[[str, dict, dict], None]] = None,
        temperature: float = 0.7,
        tools: Optional[List[dict]] = None,
        tool_context: Optional[ToolContext] = None,
        max_tool_rounds: int = 8,
    ) -> None:
        """
        Launch streaming chat completion on a background worker thread.

        When `tools` are provided, the worker runs an agentic loop: if the
        model emits tool calls they are executed via the shared tool registry
        and their results fed back for another round, until the model answers.
        """
        if self.is_generating():
            if on_error:
                on_error("A generation request is already in progress.")
            return

        self._stop_event.clear()

        sys_prompt = self._with_runtime_context(
            system_prompt if system_prompt is not None else self.default_system_prompt
        )
        # Gently point the model at the tools it has, so it reaches for them.
        if tools:
            tool_names = ", ".join(t["function"]["name"] for t in tools)
            sys_prompt = (
                f"{sys_prompt} You can use these tools when useful: {tool_names}. "
                "Call them by emitting <tool_call> blocks exactly in your output."
            )
        payload_messages = []
        if sys_prompt:
            payload_messages.append({"role": "system", "content": sys_prompt})
        payload_messages.extend(messages)

        self._active_thread = threading.Thread(
            target=self._worker_stream,
            args=(
                payload_messages,
                temperature,
                on_token,
                on_complete,
                on_error,
                on_tool_call,
                tools,
                tool_context,
                max_tool_rounds,
            ),
            daemon=True
        )
        self._active_thread.start()

    def _worker_stream(
        self,
        messages: List[Dict[str, Any]],
        temperature: float,
        on_token: Optional[Callable[[str], None]],
        on_complete: Optional[Callable[[str], None]],
        on_error: Optional[Callable[[str], None]],
        on_tool_call: Optional[Callable[[str, dict, dict], None]],
        tools: Optional[List[dict]],
        tool_context,
        max_tool_rounds: int,
    ) -> None:
        """Worker thread: agentic loop of native llama.cpp streaming generation."""
        try:
            self._ensure_model_loaded()
        except Exception as e:
            if on_error:
                on_error(f"Failed to load model: {e}")
            self._active_thread = None
            return

        working_messages: List[Dict[str, Any]] = list(messages)
        task_tool_available = any(
            tool.get("function", {}).get("name") == "tasks" for tool in (tools or [])
        )
        task_request = task_tool_available and bool(
            _TASK_REQUEST_RE.search(_latest_user_text(working_messages))
        )
        grounding_retries = 0
        tool_rounds = 0
        try:
            for _attempt in range(max_tool_rounds + 2):
                structured_calls, round_text = self._generate_round(
                    working_messages,
                    temperature,
                    tools,
                    None if task_request else on_token,
                )

                # Qwen emits tool calls either via the structured channel or as
                # text <tool_call> blocks — support both.
                text_calls = [] if structured_calls else _parse_qwen_tool_calls(round_text)
                tool_calls = structured_calls or text_calls

                if not tool_calls:
                    # Final answer: strip any tool blocks for display/persistence.
                    final_text = _strip_tool_blocks(round_text)
                    if task_request and _TASK_SUCCESS_CLAIM_RE.search(final_text):
                        if grounding_retries == 0:
                            grounding_retries += 1
                            working_messages.extend(
                                [
                                    {"role": "assistant", "content": final_text},
                                    {
                                        "role": "user",
                                        "content": (
                                            "<tool_response>{\"status\":\"not_executed\","
                                            "\"message\":\"No task was created. Call tasks.add "
                                            "successfully before claiming it was scheduled.\"}"
                                            "</tool_response>"
                                        ),
                                    },
                                ]
                            )
                            continue
                        final_text = (
                            "I couldn't create the task because I did not receive "
                            "a successful response from the tasks tool."
                        )
                    if task_request and on_token:
                        on_token(final_text)
                    if on_complete:
                        on_complete(final_text)
                    return

                if tool_rounds >= max_tool_rounds:
                    if on_error:
                        on_error(
                            f"Tool calling reached the maximum of {max_tool_rounds} rounds."
                        )
                    return
                tool_rounds += 1

                logger.info(
                    "Local LLM called %d tool(s): %s",
                    len(tool_calls),
                    [tc["name"] for tc in tool_calls],
                )

                if structured_calls:
                    # Structured channel: assistant tool_calls + tool results.
                    working_messages.append(
                        {
                            "role": "assistant",
                            "content": round_text or None,
                            "tool_calls": [
                                {
                                    "id": tc["id"],
                                    "type": "function",
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": json.dumps(tc["args"]),
                                    },
                                }
                                for tc in tool_calls
                            ],
                        }
                    )
                else:
                    # Text format: the model's own block stays as context, and
                    # results come back as <tool_response> user messages.
                    working_messages.append(
                        {"role": "assistant", "content": round_text}
                    )

                created_confirmation: Optional[str] = None
                for tc in tool_calls:
                    result = dispatch_tool_call(tc["name"], tc["args"], tool_context)
                    if tc["name"] == "tasks":
                        logger.info(
                            "Local tasks tool args=%s result=%s",
                            tc["args"],
                            result,
                        )
                        created_confirmation = (
                            _task_created_confirmation(result) or created_confirmation
                        )
                    if on_tool_call:
                        try:
                            on_tool_call(tc["name"], tc["args"], result)
                        except Exception:
                            pass
                    if structured_calls:
                        working_messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": json.dumps(result),
                            }
                        )
                    else:
                        working_messages.append(
                            {
                                "role": "user",
                                "content": f"<tool_response>{json.dumps(result)}</tool_response>",
                            }
                        )
                if created_confirmation is not None:
                    if on_token:
                        on_token(created_confirmation)
                    if on_complete:
                        on_complete(created_confirmation)
                    return
            if on_error:
                on_error("The model could not complete the tool workflow reliably.")
        except Exception as e:
            err_msg = f"Error during local llama.cpp generation: {e}"
            logger.error(err_msg)
            if on_error:
                on_error(err_msg)
        finally:
            self._active_thread = None

    def _generate_round(
        self,
        messages: List[Dict[str, Any]],
        temperature: float,
        tools: Optional[List[dict]],
        on_token: Optional[Callable[[str], None]],
    ) -> tuple[List[dict], str]:
        """Stream one completion. Returns (structured_tool_calls, round_text).

        Text tokens are streamed to on_token live, except Qwen text-format
        <tool_call> blocks, which are hidden from the UI.
        """
        token_parts: List[str] = []
        accumulated_calls: List[dict] = []
        finish_reason: Optional[str] = None

        with self._llm_lock:
            streamer = self._llm.create_chat_completion(
                messages=messages,
                stream=True,
                temperature=temperature,
                tools=tools,
            )
            for chunk in streamer:
                if self._stop_event.is_set():
                    logger.info("Local LLM generation stopped by user request.")
                    break

                choice = chunk["choices"][0]
                delta = choice.get("delta", {})

                content = delta.get("content")
                if content:
                    token_parts.append(content)
                    round_text = "".join(token_parts)
                    # Hide anything inside an unclosed Qwen <tool_call> block.
                    inside_block = round_text.rfind("<tool_call>") > round_text.rfind(
                        "</tool_call>"
                    )
                    if not inside_block and on_token:
                        on_token(content)

                new_calls = delta.get("tool_calls")
                if new_calls:
                    self._merge_tool_calls(accumulated_calls, new_calls)

                if choice.get("finish_reason"):
                    finish_reason = choice.get("finish_reason")

        assembled_text = "".join(token_parts)
        if finish_reason == "tool_calls" and accumulated_calls:
            calls: List[dict] = []
            for entry in accumulated_calls:
                try:
                    args = json.loads(entry["arguments"] or "{}")
                except json.JSONDecodeError:
                    logger.warning(
                        "Local LLM produced malformed tool arguments: %r", entry["arguments"]
                    )
                    args = {}
                calls.append(
                    {
                        "id": entry.get("id") or f"call_{len(calls)}",
                        "name": entry["name"],
                        "args": args,
                    }
                )
            return calls, assembled_text
        return [], assembled_text

    @staticmethod
    def _merge_tool_calls(accumulated: List[dict], new_calls: List[dict]) -> None:
        """Merge streamed tool-call fragments (arguments arrive piecemeal)."""
        for new in new_calls or []:
            index = new.get("index", 0)
            while len(accumulated) <= index:
                accumulated.append({"id": None, "name": "", "arguments": ""})
            entry = accumulated[index]
            function = new.get("function", {})
            if function.get("name"):
                entry["name"] = function["name"]
            if function.get("arguments"):
                entry["arguments"] += function["arguments"]
            if new.get("id"):
                entry["id"] = new["id"]
