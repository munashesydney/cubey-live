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
from src.client.tools.registry import ToolContext, dispatch_tool_call
from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

# Qwen's text-based function calling format (this GGUF emits tool calls as
# text, not via the structured channel).
_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)
_TOOL_CALL_FUNC_RE = re.compile(r"<function=([^>]+)>")
_TOOL_CALL_PARAM_RE = re.compile(r"<parameter=([^>]+)>(.*?)</parameter>", re.DOTALL)


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
        max_tool_rounds: int = 4,
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

        sys_prompt = system_prompt if system_prompt is not None else self.default_system_prompt
        payload: List[Dict[str, Any]] = []
        if sys_prompt:
            payload.append({"role": "system", "content": sys_prompt})
        payload.extend(messages)

        # _worker_stream is synchronous and invokes on_complete/on_error
        # before returning, so no event is needed.
        self._worker_stream(
            payload, temperature, None, on_complete, on_error, None,
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
        max_tool_rounds: int = 4,
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

        sys_prompt = system_prompt if system_prompt is not None else self.default_system_prompt
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
        try:
            for _round in range(max_tool_rounds + 1):
                structured_calls, round_text = self._generate_round(
                    working_messages, temperature, tools, on_token
                )

                # Qwen emits tool calls either via the structured channel or as
                # text <tool_call> blocks — support both.
                text_calls = [] if structured_calls else _parse_qwen_tool_calls(round_text)
                tool_calls = structured_calls or text_calls

                if not tool_calls:
                    # Final answer: strip any tool blocks for display/persistence.
                    if on_complete:
                        on_complete(_strip_tool_blocks(round_text))
                    return

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

                for tc in tool_calls:
                    result = dispatch_tool_call(tc["name"], tc["args"], tool_context)
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
            else:
                if on_error:
                    on_error(
                        f"Tool calling exceeded the maximum of {max_tool_rounds} rounds."
                    )
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
