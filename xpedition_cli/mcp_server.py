from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

from . import __version__
from .contract_gen import SCHEMA_VERSION
from .errors import CLIError
from .output import redact

MCP_PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")


class MCPServer:
    """Small, read-only MCP tools server over newline-delimited JSON-RPC."""

    def __init__(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self._handler = handler

    @staticmethod
    def tools() -> list[dict[str, Any]]:
        common_project = {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "local normalized project path"},
                "backend": {"type": "string", "enum": ["mock", "native_xpedition"]},
            },
        }
        return [
            {
                "name": "xpedition_snapshot",
                "title": "Xpedition project snapshot",
                "description": "Read a normalized Xpedition project snapshot.",
                "inputSchema": common_project,
                "outputSchema": {"type": "object", "additionalProperties": True},
            },
            {
                "name": "xpedition_query",
                "title": "Xpedition design query",
                "description": "Search normalized schematic and project records.",
                "inputSchema": {
                    "type": "object",
                    "properties": {**common_project["properties"], "query": {"type": "string"}},
                    "required": ["query"],
                },
                "outputSchema": {"type": "object", "additionalProperties": True},
            },
            {
                "name": "xpedition_review",
                "title": "Xpedition design review",
                "description": "Run deterministic review checks against a normalized project.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        **common_project["properties"],
                        "rules": {"type": "string", "description": "optional JSON rules file"},
                    },
                },
                "outputSchema": {"type": "object", "additionalProperties": True},
            },
            {
                "name": "xpedition_capabilities",
                "title": "Xpedition capabilities",
                "description": "List verified and planned Xpedition backends.",
                "inputSchema": {"type": "object", "properties": {}},
                "outputSchema": {"type": "object", "additionalProperties": True},
            },
        ]

    @staticmethod
    def _tool_method(name: str) -> str | None:
        return {
            "xpedition_snapshot": "snapshot",
            "xpedition_query": "query",
            "xpedition_review": "review",
            "xpedition_capabilities": "capabilities",
        }.get(name)

    @staticmethod
    def _error_response(
        request_id: Any, code: int, message: str, data: Any = None
    ) -> dict[str, Any]:
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        return {"jsonrpc": "2.0", "id": request_id, "error": error}

    @staticmethod
    def _tool_error(request_id: Any, error: CLIError) -> dict[str, Any]:
        envelope = {
            "ok": False,
            "schema_version": SCHEMA_VERSION,
            "error": {
                "code": error.code,
                "message": error.message,
                "details": error.details or {},
                "retryable": error.is_retryable,
            },
        }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(redact(envelope), ensure_ascii=False)}
                ],
                "structuredContent": redact(envelope),
                "isError": True,
            },
        }

    def _handle(self, request: Any) -> dict[str, Any] | None:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
            return self._error_response(None, -32600, "invalid JSON-RPC request")
        request_id = request.get("id")
        if request_id is not None and not isinstance(request_id, (str, int, float)):
            return self._error_response(None, -32600, "id must be a string, number, or null")
        method = request.get("method")
        if not isinstance(method, str):
            return self._error_response(request_id, -32600, "method must be a string")
        if method.startswith("notifications/"):
            return None
        if method == "initialize":
            initialize_params = request.get("params") or {}
            if not isinstance(initialize_params, dict):
                return self._error_response(
                    request_id, -32602, "initialize params must be an object"
                )
            requested_version = initialize_params.get("protocolVersion")
            selected_version = (
                requested_version
                if requested_version in SUPPORTED_PROTOCOL_VERSIONS
                else MCP_PROTOCOL_VERSION
            )
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": selected_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "xpedition-cli", "version": __version__},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": self.tools()}}
        if method == "tools/call":
            params = request.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("name"), str):
                return self._error_response(request_id, -32602, "tools/call requires params.name")
            tool_name = params["name"]
            tool_method = self._tool_method(tool_name)
            if tool_method is None:
                return self._error_response(request_id, -32602, f"unknown tool: {tool_name}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return self._error_response(
                    request_id, -32602, "tools/call arguments must be an object"
                )
            try:
                data = self._handler(tool_method, arguments)
            except CLIError as error:
                return self._tool_error(request_id, error)
            text = json.dumps(redact(data), ensure_ascii=False, separators=(",", ":"))
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": redact(data),
                    "isError": False,
                },
            }
        return self._error_response(request_id, -32601, f"method not found: {method}")

    def serve(self, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
        input_stream = stdin or sys.stdin
        output_stream = stdout or sys.stdout
        for line in input_stream:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = self._error_response(None, -32700, "parse error")
            else:
                response = self._handle(request)
            if response is not None:
                output_stream.write(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n"
                )
                output_stream.flush()
        return 0
