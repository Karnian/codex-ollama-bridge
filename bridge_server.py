#!/usr/bin/env python3
"""Ollama-shaped bridge server backed by local Codex CLI.

Exposes:
- POST /api/chat
- POST /api/generate

This server accepts Ollama-like request payloads and maps them to a Codex
non-interactive call (`codex exec --json`). Responses are returned in an
Ollama-like shape.
"""

from __future__ import annotations

import json
import os
import ssl
import subprocess
import threading
import time
import uuid
from getpass import getpass
from dataclasses import dataclass
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest
from zoneinfo import ZoneInfo


DEFAULT_PORT = int(os.environ.get("BRIDGE_PORT", "11435"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
GEMINI_BIN = os.environ.get("GEMINI_BIN", "gemini")
GEMINI_API_BASE_URL = os.environ.get("GEMINI_API_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
CODEX_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TIMEOUT_SECONDS", "120"))
STARTUP_CHECK_TIMEOUT_SECONDS = int(os.environ.get("STARTUP_CHECK_TIMEOUT_SECONDS", "15"))
STARTUP_CHECK_STRICT = os.environ.get("STARTUP_CHECK_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}
CODEX_MODEL = os.environ.get("CODEX_MODEL", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "").strip()
CODEX_MODEL_VERBOSITY = os.environ.get("CODEX_MODEL_VERBOSITY", "high").strip().lower()
BRIDGE_MODEL_NAME = os.environ.get("BRIDGE_MODEL_NAME", "codex").strip() or "codex"
DETAIL_MODE = os.environ.get("DETAIL_MODE", "high").strip().lower()
DETAIL_SYSTEM_INSTRUCTION = os.environ.get(
    "DETAIL_SYSTEM_INSTRUCTION",
    "Always respond in the user's language environment and match the language used in the user's request unless explicitly asked otherwise. Respond naturally and conversationally. Prefer flowing prose and avoid forced numbered or bullet lists unless the user explicitly asks for list format. Give enough detail to be useful while keeping the flow smooth and readable.",
).strip()

KST = ZoneInfo("Asia/Seoul")
LOG_DIR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
SETTINGS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge_settings.json")
SECRETS_FILE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bridge_secrets.json")
active_log_file_path = ""
LOG_FILE_LOCK = threading.Lock()
CONSOLE_LOG_VALUE_MAX_CHARS = 200
gemini_auth_mode = "google"


def resolve_gemini_model_name(requested_model: str) -> str:
    normalized = requested_model.strip().lower()
    if normalized and normalized != "gemini":
        return requested_model.strip()
    if GEMINI_MODEL:
        return GEMINI_MODEL
    return "gemini-2.5-flash"


def build_gemini_ssl_context() -> ssl.SSLContext:
    return ssl._create_unverified_context()  # noqa: SLF001


@dataclass
class BridgeResult:
    text: str
    raw_events: list[dict[str, Any]]


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="microseconds")


def append_log_text(text: str) -> None:
    log_file_path = active_log_file_path
    if not log_file_path:
        os.makedirs(LOG_DIR_PATH, exist_ok=True)
        fallback_name = datetime.now(KST).strftime("bridge_server-%Y%m%d-%H%M%S.log")
        log_file_path = os.path.join(LOG_DIR_PATH, fallback_name)
    with LOG_FILE_LOCK:
        with open(log_file_path, "a", encoding="utf-8") as fp:
            fp.write(text)
            if not text.endswith("\n"):
                fp.write("\n")


def log_line(text: str) -> None:
    print(text, flush=True)
    append_log_text(text)


def truncate_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3] + "..."


def truncate_for_console(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return truncate_text(value, max_chars)
    if isinstance(value, dict):
        return {k: truncate_for_console(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_for_console(item, max_chars) for item in value]
    if isinstance(value, tuple):
        return [truncate_for_console(item, max_chars) for item in value]
    return value


def chunk_text(text: str, chunk_size: int = 40) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]


def build_prompt_from_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if DETAIL_MODE != "off" and DETAIL_SYSTEM_INSTRUCTION:
        lines.append(f"[SYSTEM] {DETAIL_SYSTEM_INSTRUCTION}")
    for msg in messages:
        role = str(msg.get("role", "user")).upper()
        content = str(msg.get("content", ""))
        lines.append(f"[{role}] {content}")
    lines.append("\nAnswer as the assistant only.")
    return "\n".join(lines)


def run_codex(prompt: str, timeout_seconds: int | None = None) -> BridgeResult:
    cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "--json"]
    if CODEX_MODEL:
        cmd.extend(["--model", CODEX_MODEL])
    if CODEX_MODEL_VERBOSITY in {"low", "medium", "high"}:
        cmd.extend(["-c", f'model_verbosity="{CODEX_MODEL_VERBOSITY}"'])
    cmd.append("-")

    env = os.environ.copy()
    env.setdefault("CI", "true")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    proc = subprocess.run(
        cmd,
        input=prompt,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds if timeout_seconds is not None else CODEX_TIMEOUT_SECONDS,
        check=False,
        env=env,
    )

    events: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    if proc.returncode != 0:
        err = proc.stderr.strip() or "codex exec failed"
        raise RuntimeError(err)

    answer = ""
    for ev in events:
        if ev.get("type") != "item.completed":
            continue
        item = ev.get("item", {})
        if item.get("type") in {"agent_message", "agentMessage"}:
            answer = str(item.get("text", ""))

    if not answer:
        raise RuntimeError("No assistant message found in codex output")

    return BridgeResult(text=answer, raw_events=events)


def run_gemini_cli(prompt: str, requested_model: str, timeout_seconds: int | None = None) -> BridgeResult:
    cmd = [GEMINI_BIN, "--prompt", prompt]
    gemini_model = resolve_gemini_model_name(requested_model)
    if gemini_model:
        cmd.extend(["--model", gemini_model])

    env = os.environ.copy()
    env.pop("CI", None)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.pop("GEMINI_API_KEY", None)
    env.pop("GOOGLE_API_KEY", None)
    env["GOOGLE_GENAI_USE_GCA"] = "true"

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds if timeout_seconds is not None else CODEX_TIMEOUT_SECONDS,
        check=False,
        env=env,
    )

    if proc.returncode != 0:
        err = proc.stderr.strip() or "gemini cli call failed"
        raise RuntimeError(err)

    answer = proc.stdout.strip()
    if not answer:
        raise RuntimeError("No assistant message found in gemini output")

    return BridgeResult(text=answer, raw_events=[])


def run_gemini_api(prompt: str, requested_model: str, timeout_seconds: int | None = None) -> BridgeResult:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Gemini auth mode is 'api' but GEMINI_API_KEY is not set")

    model_name = resolve_gemini_model_name(requested_model)
    endpoint = f"{GEMINI_API_BASE_URL}/models/{model_name}:generateContent?key={api_key}"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ]
    }
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    timeout = timeout_seconds if timeout_seconds is not None else CODEX_TIMEOUT_SECONDS
    ssl_context = build_gemini_ssl_context()
    try:
        with urlrequest.urlopen(req, timeout=timeout, context=ssl_context) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urlerror.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"gemini api call failed ({exc.code}): {detail}") from exc
    except urlerror.URLError as exc:
        raise RuntimeError(f"gemini api call failed: {exc.reason}") from exc

    parsed = json.loads(raw)
    candidates = parsed.get("candidates", [])
    if not isinstance(candidates, list) or not candidates:
        raise RuntimeError(f"gemini api returned no candidates: {raw}")
    content = candidates[0].get("content", {})
    parts = content.get("parts", []) if isinstance(content, dict) else []
    text_parts: list[str] = []
    for part in parts:
        if isinstance(part, dict):
            text = str(part.get("text", ""))
            if text:
                text_parts.append(text)
    answer = "\n".join(text_parts).strip()
    if not answer:
        raise RuntimeError(f"gemini api returned empty text: {raw}")
    return BridgeResult(text=answer, raw_events=[])


def run_gemini(prompt: str, requested_model: str, timeout_seconds: int | None = None) -> BridgeResult:
    if gemini_auth_mode == "api":
        return run_gemini_api(prompt, requested_model, timeout_seconds=timeout_seconds)
    return run_gemini_cli(prompt, requested_model, timeout_seconds=timeout_seconds)


def resolve_runner(model_name: str) -> tuple[str, str]:
    normalized = model_name.strip().lower()
    if not normalized or normalized.startswith("codex"):
        return "codex", model_name.strip() or "codex"
    if normalized.startswith("gemini"):
        return "gemini", model_name.strip()
    raise ValueError("model must start with 'codex' or 'gemini'")


def run_model(model_name: str, prompt: str, timeout_seconds: int | None = None) -> BridgeResult:
    runner, resolved = resolve_runner(model_name)
    if runner == "codex":
        return run_codex(prompt, timeout_seconds=timeout_seconds)
    return run_gemini(prompt, resolved, timeout_seconds=timeout_seconds)


def startup_probe(model_name: str, timeout_seconds: int) -> tuple[bool, str]:
    probe_prompt = "Reply with one short word only: OK"
    try:
        result = run_model(model_name, probe_prompt, timeout_seconds=timeout_seconds)
    except Exception as exc:
        return False, str(exc)
    preview = result.text.strip().replace("\n", " ")
    if len(preview) > 80:
        preview = preview[:77] + "..."
    return True, preview


def load_settings() -> dict[str, Any]:
    try:
        with open(SETTINGS_FILE_PATH, "r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if isinstance(loaded, dict):
            return loaded
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def save_settings(settings: dict[str, Any]) -> None:
    with open(SETTINGS_FILE_PATH, "w", encoding="utf-8") as fp:
        json.dump(settings, fp, ensure_ascii=False, indent=2, sort_keys=True)


def load_secrets() -> dict[str, Any]:
    try:
        with open(SECRETS_FILE_PATH, "r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if isinstance(loaded, dict):
            return loaded
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    return {}


def save_secrets(secrets: dict[str, Any]) -> None:
    with open(SECRETS_FILE_PATH, "w", encoding="utf-8") as fp:
        json.dump(secrets, fp, ensure_ascii=False, indent=2, sort_keys=True)


def choose_gemini_auth_mode_interactive(default_mode: str) -> str:
    log_line("[setup] Gemini auth mode is not configured.")
    log_line("[setup] Choose Gemini auth mode: [1] google (default), [2] api")
    answer = input("Select mode (Enter=1): ").strip().lower()
    if answer in {"2", "api", "apikey", "api-key"}:
        return "api"
    if answer in {"1", "google", "", "g"}:
        return "google"
    log_line(f"[setup] Unknown choice '{answer}'. Using default: {default_mode}")
    return default_mode


def ensure_gemini_auth_mode() -> str:
    env_mode = os.environ.get("GEMINI_AUTH_MODE", "").strip().lower()
    if env_mode in {"google", "api"}:
        return env_mode

    settings = load_settings()
    saved_mode = str(settings.get("gemini_auth_mode", "")).strip().lower()
    if saved_mode in {"google", "api"}:
        return saved_mode

    default_mode = "google"
    selected_mode = default_mode
    if os.isatty(0):
        selected_mode = choose_gemini_auth_mode_interactive(default_mode)

    settings["gemini_auth_mode"] = selected_mode
    save_settings(settings)
    return selected_mode


def ensure_api_key_for_gemini_if_needed(mode: str) -> None:
    if mode != "api":
        return

    existing_key = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip()
    if existing_key:
        os.environ["GEMINI_API_KEY"] = existing_key
        os.environ["GOOGLE_API_KEY"] = existing_key
        secrets = load_secrets()
        if str(secrets.get("gemini_api_key", "")).strip() != existing_key:
            secrets["gemini_api_key"] = existing_key
            save_secrets(secrets)
        return

    secrets = load_secrets()
    saved_key = str(secrets.get("gemini_api_key", "")).strip()
    if saved_key:
        os.environ["GEMINI_API_KEY"] = saved_key
        os.environ["GOOGLE_API_KEY"] = saved_key
        return

    if not os.isatty(0):
        return

    log_line("[setup] Gemini API mode selected but API key is missing.")
    entered_key = getpass("Enter GEMINI_API_KEY (input hidden): ").strip()
    if entered_key:
        os.environ["GEMINI_API_KEY"] = entered_key
        os.environ["GOOGLE_API_KEY"] = entered_key
        secrets["gemini_api_key"] = entered_key
        save_secrets(secrets)


def json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def print_pretty_json(payload: dict[str, Any]) -> None:
    full_rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    console_rendered = json.dumps(
        truncate_for_console(payload, CONSOLE_LOG_VALUE_MAX_CHARS),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    print(console_rendered, flush=True)
    append_log_text(full_rendered)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "CodexOllamaBridge/0.1"
    _bridge_request_id: str = ""

    def _request_id(self) -> str:
        current = getattr(self, "_bridge_request_id", "")
        if current:
            return current
        generated = uuid.uuid4().hex[:8]
        self._bridge_request_id = generated
        return generated

    def _log(self, event: str, **fields: Any) -> None:
        payload = {
            "ts": now_iso(),
            "request_id": self._request_id(),
            "method": self.command,
            "path": self.path,
            "event": event,
            **fields,
        }
        print_pretty_json(payload)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            payload = {"ok": True, "time": now_iso()}
            json_response(self, HTTPStatus.OK, payload)
            self._log("response.sent", status=int(HTTPStatus.OK), response=payload)
            return

        if self.path == "/api/tags":
            payload = self.tags_payload()
            json_response(self, HTTPStatus.OK, payload)
            self._log("response.sent", status=int(HTTPStatus.OK), response=payload)
            return

        payload = {"error": "Not found"}
        json_response(self, HTTPStatus.NOT_FOUND, payload)
        self._log("response.sent", status=int(HTTPStatus.NOT_FOUND), response=payload)

    def tags_payload(self) -> dict[str, Any]:
        model_names = ["codex", "gemini"]
        return {
            "models": [
                {
                    "name": name,
                    "model": name,
                    "modified_at": now_iso(),
                    "size": 0,
                    "digest": f"{name}-bridge",
                    "details": {
                        "parent_model": "",
                        "format": "bridge",
                        "family": name,
                        "families": [name],
                        "parameter_size": "unknown",
                        "quantization_level": "none",
                    },
                }
                for name in model_names
            ]
        }

    def do_POST(self) -> None:  # noqa: N802
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(content_length) if content_length else b"{}"
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._log("request.invalid_json")
            error_payload = {"error": "Invalid JSON body"}
            json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
            self._log("response.sent", status=int(HTTPStatus.BAD_REQUEST), response=error_payload)
            return

        if self.path == "/api/chat":
            self.handle_chat(payload)
            return

        if self.path == "/api/generate":
            self.handle_generate(payload)
            return

        error_payload = {"error": "Not found"}
        json_response(self, HTTPStatus.NOT_FOUND, error_payload)
        self._log("response.sent", status=int(HTTPStatus.NOT_FOUND), response=error_payload)

    def handle_chat(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model", BRIDGE_MODEL_NAME))
        messages = payload.get("messages", [])
        stream = bool(payload.get("stream", False))

        self._log("chat.start", request=payload)

        if not isinstance(messages, list) or not messages:
            error_payload = {"error": "messages must be a non-empty list"}
            json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
            self._log("chat.error", status=int(HTTPStatus.BAD_REQUEST), error=error_payload["error"])
            return

        try:
            result = run_model(model, build_prompt_from_messages(messages))
        except ValueError as exc:
            error_payload = {"error": str(exc)}
            json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
            self._log("chat.error", status=int(HTTPStatus.BAD_REQUEST), error=str(exc))
            return
        except Exception as exc:
            error_payload = {"error": str(exc)}
            json_response(self, HTTPStatus.BAD_GATEWAY, error_payload)
            self._log("chat.error", status=int(HTTPStatus.BAD_GATEWAY), error=str(exc))
            return

        if stream:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.end_headers()
            chunks = 0
            chars = 0
            for piece in chunk_text(result.text):
                chunks += 1
                chars += len(piece)
                chunk = {
                    "model": model,
                    "created_at": now_iso(),
                    "message": {"role": "assistant", "content": piece},
                    "done": False,
                }
                self.wfile.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.01)
            done = {
                "model": model,
                "created_at": now_iso(),
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
            }
            self.wfile.write((json.dumps(done, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
            self._log(
                "chat.stream.done",
                status=int(HTTPStatus.OK),
                chunks=chunks,
                chars=chars,
                response_text=result.text,
            )
            return

        response = {
            "model": model,
            "created_at": now_iso(),
            "message": {"role": "assistant", "content": result.text},
            "done": True,
            "done_reason": "stop",
            "total_duration": 0,
        }
        json_response(self, HTTPStatus.OK, response)
        self._log(
            "chat.done",
            status=int(HTTPStatus.OK),
            response=response,
        )

    def handle_generate(self, payload: dict[str, Any]) -> None:
        model = str(payload.get("model", BRIDGE_MODEL_NAME))
        prompt = str(payload.get("prompt", "")).strip()
        system = str(payload.get("system", "")).strip()
        stream = bool(payload.get("stream", False))

        self._log("generate.start", request=payload)

        if not prompt:
            error_payload = {"error": "prompt is required"}
            json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
            self._log("generate.error", status=int(HTTPStatus.BAD_REQUEST), error=error_payload["error"])
            return

        prompt_parts: list[str] = []
        if DETAIL_MODE != "off" and DETAIL_SYSTEM_INSTRUCTION:
            prompt_parts.append(f"[SYSTEM] {DETAIL_SYSTEM_INSTRUCTION}")
        if system:
            prompt_parts.append(f"[SYSTEM] {system}")
        prompt_parts.append(f"[USER] {prompt}")
        full_prompt = "\n".join(prompt_parts)

        try:
            result = run_model(model, full_prompt)
        except ValueError as exc:
            error_payload = {"error": str(exc)}
            json_response(self, HTTPStatus.BAD_REQUEST, error_payload)
            self._log("generate.error", status=int(HTTPStatus.BAD_REQUEST), error=str(exc))
            return
        except Exception as exc:
            error_payload = {"error": str(exc)}
            json_response(self, HTTPStatus.BAD_GATEWAY, error_payload)
            self._log("generate.error", status=int(HTTPStatus.BAD_GATEWAY), error=str(exc))
            return

        if stream:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
            self.end_headers()
            chunks = 0
            chars = 0
            for piece in chunk_text(result.text):
                chunks += 1
                chars += len(piece)
                chunk = {
                    "model": model,
                    "created_at": now_iso(),
                    "response": piece,
                    "done": False,
                }
                self.wfile.write((json.dumps(chunk, ensure_ascii=False) + "\n").encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.01)
            done = {
                "model": model,
                "created_at": now_iso(),
                "response": "",
                "done": True,
                "done_reason": "stop",
            }
            self.wfile.write((json.dumps(done, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
            self._log(
                "generate.stream.done",
                status=int(HTTPStatus.OK),
                chunks=chunks,
                chars=chars,
                response_text=result.text,
            )
            return

        response = {
            "model": model,
            "created_at": now_iso(),
            "response": result.text,
            "done": True,
            "done_reason": "stop",
            "total_duration": 0,
        }
        json_response(self, HTTPStatus.OK, response)
        self._log(
            "generate.done",
            status=int(HTTPStatus.OK),
            response=response,
        )

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        request_id = uuid.uuid4().hex[:8]
        log_line(f"[{now_iso()}] [{request_id}] {self.address_string()} - {format % args}")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    global active_log_file_path, gemini_auth_mode

    host = "0.0.0.0"
    port = DEFAULT_PORT
    os.makedirs(LOG_DIR_PATH, exist_ok=True)
    start_name = datetime.now(KST).strftime("bridge_server-%Y%m%d-%H%M%S.log")
    active_log_file_path = os.path.join(LOG_DIR_PATH, start_name)

    gemini_auth_mode = ensure_gemini_auth_mode()
    ensure_api_key_for_gemini_if_needed(gemini_auth_mode)

    log_line(f"[{now_iso()}] Starting bridge on http://{host}:{port}")
    log_line(f"[{now_iso()}] Log file: {active_log_file_path}")
    log_line(f"[{now_iso()}] Gemini auth mode: {gemini_auth_mode}")
    log_line(f"[{now_iso()}] Using codex binary: {CODEX_BIN}")
    log_line(f"[{now_iso()}] Using gemini binary: {GEMINI_BIN}")
    log_line(f"[{now_iso()}] Using model verbosity: {CODEX_MODEL_VERBOSITY or 'default'}")
    log_line(f"[{now_iso()}] Detail mode: {DETAIL_MODE}")

    checks = ["codex", "gemini"]
    check_results: dict[str, tuple[bool, str]] = {}
    log_line(f"[{now_iso()}] Running startup AI readiness checks...")
    for name in checks:
        ok, detail = startup_probe(name, timeout_seconds=STARTUP_CHECK_TIMEOUT_SECONDS)
        check_results[name] = (ok, detail)
        if ok:
            log_line(f"[{now_iso()}] [READY] {name}: {detail}")
        else:
            log_line(f"[{now_iso()}] [FAIL ] {name}: {detail}")

    if STARTUP_CHECK_STRICT and any(not ok for ok, _ in check_results.values()):
        raise RuntimeError("Startup readiness checks failed and STARTUP_CHECK_STRICT is enabled")

    server = ReusableThreadingHTTPServer((host, port), BridgeHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
