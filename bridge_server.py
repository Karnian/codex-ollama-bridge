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
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


DEFAULT_PORT = int(os.environ.get("BRIDGE_PORT", "11435"))
CODEX_BIN = os.environ.get("CODEX_BIN", "codex")
CODEX_TIMEOUT_SECONDS = int(os.environ.get("CODEX_TIMEOUT_SECONDS", "120"))
CODEX_MODEL = os.environ.get("CODEX_MODEL", "").strip()
CODEX_MODEL_VERBOSITY = os.environ.get("CODEX_MODEL_VERBOSITY", "high").strip().lower()
BRIDGE_MODEL_NAME = os.environ.get("BRIDGE_MODEL_NAME", "codex").strip() or "codex"
LOG_VALUE_MAX_CHARS = int(os.environ.get("LOG_VALUE_MAX_CHARS", "200"))
DETAIL_MODE = os.environ.get("DETAIL_MODE", "high").strip().lower()
DETAIL_SYSTEM_INSTRUCTION = os.environ.get(
    "DETAIL_SYSTEM_INSTRUCTION",
    "Always respond in the user's language environment and match the language used in the user's request unless explicitly asked otherwise. Respond naturally and conversationally. Prefer flowing prose and avoid forced numbered or bullet lists unless the user explicitly asks for list format. Give enough detail to be useful while keeping the flow smooth and readable.",
).strip()


@dataclass
class BridgeResult:
    text: str
    raw_events: list[dict[str, Any]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def run_codex(prompt: str) -> BridgeResult:
    cmd = [CODEX_BIN, "exec", "--skip-git-repo-check", "--json"]
    if CODEX_MODEL:
        cmd.extend(["--model", CODEX_MODEL])
    if CODEX_MODEL_VERBOSITY in {"low", "medium", "high"}:
        cmd.extend(["-c", f'model_verbosity="{CODEX_MODEL_VERBOSITY}"'])
    cmd.append(prompt)

    env = os.environ.copy()
    env.setdefault("CI", "true")
    env.setdefault("GIT_TERMINAL_PROMPT", "0")

    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=CODEX_TIMEOUT_SECONDS,
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


def json_response(handler: BaseHTTPRequestHandler, code: int, payload: dict[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def print_pretty_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)


def truncate_text(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    if max_chars <= 3:
        return value[:max_chars]
    return value[: max_chars - 3] + "..."


def truncate_for_log(value: Any, max_chars: int) -> Any:
    if isinstance(value, str):
        return truncate_text(value, max_chars)
    if isinstance(value, dict):
        return {k: truncate_for_log(v, max_chars) for k, v in value.items()}
    if isinstance(value, list):
        return [truncate_for_log(item, max_chars) for item in value]
    if isinstance(value, tuple):
        return [truncate_for_log(item, max_chars) for item in value]
    return value


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "CodexOllamaBridge/0.1"

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
        print_pretty_json(truncate_for_log(payload, LOG_VALUE_MAX_CHARS))

    def do_GET(self) -> None:  # noqa: N802
        self._log("request.received")
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
        model_name = BRIDGE_MODEL_NAME
        return {
            "models": [
                {
                    "name": model_name,
                    "model": model_name,
                    "modified_at": now_iso(),
                    "size": 0,
                    "digest": "codex-bridge",
                    "details": {
                        "parent_model": "",
                        "format": "bridge",
                        "family": "codex",
                        "families": ["codex"],
                        "parameter_size": "unknown",
                        "quantization_level": "none",
                    },
                }
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

        self._log("request.received", request=payload)

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
            result = run_codex(build_prompt_from_messages(messages))
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
            result = run_codex(full_prompt)
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
        print(f"[{request_id}] {self.address_string()} - {format % args}")


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True


def main() -> None:
    host = "0.0.0.0"
    port = DEFAULT_PORT
    print(f"Starting bridge on http://{host}:{port}")
    print(f"Using codex binary: {CODEX_BIN}")
    print(f"Using model verbosity: {CODEX_MODEL_VERBOSITY or 'default'}")
    print(f"Detail mode: {DETAIL_MODE}")
    server = ReusableThreadingHTTPServer((host, port), BridgeHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
