from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = "1.0"
JSONRPC_VERSION = "2.0"
MAX_FRAME_BYTES = 1024 * 1024
METHOD_NAME = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")


class RpcProtocolError(ValueError):
    def __init__(self, code: int, message: str, *, request_id: object = None) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


@dataclass(frozen=True, slots=True)
class RpcRequest:
    request_id: str | int
    method: str
    params: dict[str, Any]
    protocol_version: str


def parse_request(frame: bytes) -> RpcRequest:
    if not frame or len(frame) > MAX_FRAME_BYTES:
        raise RpcProtocolError(-32600, "frame is empty or exceeds 1 MiB")
    try:
        value = json.loads(frame)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RpcProtocolError(-32700, "invalid JSON frame") from error
    if not isinstance(value, dict):
        raise RpcProtocolError(-32600, "request must be a JSON object")
    request_id = value.get("id")
    if isinstance(request_id, bool) or not isinstance(request_id, (str, int)):
        raise RpcProtocolError(-32600, "request id must be a string or integer")
    if isinstance(request_id, str) and (not request_id or len(request_id) > 128):
        raise RpcProtocolError(-32600, "string request id length is invalid", request_id=request_id)
    if value.get("jsonrpc") != JSONRPC_VERSION:
        raise RpcProtocolError(-32600, "jsonrpc must be 2.0", request_id=request_id)
    method = value.get("method")
    if not isinstance(method, str) or not METHOD_NAME.fullmatch(method):
        raise RpcProtocolError(-32600, "method name is invalid", request_id=request_id)
    params = value.get("params", {})
    if not isinstance(params, dict):
        raise RpcProtocolError(-32602, "params must be an object", request_id=request_id)
    protocol_version = value.get("protocolVersion")
    if protocol_version != PROTOCOL_VERSION:
        raise RpcProtocolError(
            -32600,
            f"unsupported protocolVersion: {protocol_version}",
            request_id=request_id,
        )
    return RpcRequest(request_id, method, params, protocol_version)


def success_response(request_id: str | int, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def error_response(error: RpcProtocolError) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": error.request_id,
        "error": {"code": error.code, "message": str(error)},
    }


def encode_message(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
