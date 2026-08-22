from __future__ import annotations

import json
import subprocess
import sys

import pytest

from aegisrun.sidecar.dispatcher import dispatch
from aegisrun.sidecar.protocol import (
    MAX_FRAME_BYTES,
    RpcProtocolError,
    encode_message,
    parse_request,
)


def _request(method: str, *, request_id: str = "req-1") -> bytes:
    return encode_message(
        {
            "jsonrpc": "2.0",
            "protocolVersion": "1.0",
            "id": request_id,
            "method": method,
            "params": {},
        }
    )


def test_protocol_parses_versioned_request_and_rejects_noise() -> None:
    request = parse_request(_request("system.health"))

    assert request.method == "system.health"
    assert request.protocol_version == "1.0"
    with pytest.raises(RpcProtocolError) as malformed:
        parse_request(b"sidecar started\n")
    assert malformed.value.code == -32700


def test_protocol_rejects_wrong_version_params_and_oversized_frame() -> None:
    wrong_version = _request("system.health").replace(b'"1.0"', b'"2.0"')
    with pytest.raises(RpcProtocolError, match="unsupported protocolVersion"):
        parse_request(wrong_version)
    with pytest.raises(RpcProtocolError, match="params must be an object"):
        parse_request(
            b'{"jsonrpc":"2.0","protocolVersion":"1.0","id":1,'
            b'"method":"system.health","params":[]}\n'
        )
    with pytest.raises(RpcProtocolError, match="exceeds"):
        parse_request(b"x" * (MAX_FRAME_BYTES + 1))


@pytest.mark.asyncio
async def test_dispatcher_exposes_health_and_capabilities() -> None:
    health = await dispatch(parse_request(_request("system.health")))
    capabilities = await dispatch(parse_request(_request("system.capabilities")))
    bootstrap = await dispatch(parse_request(_request("system.bootstrap")))

    assert health["status"] == "ok"
    assert health["transport"] == "stdio-ndjson"
    assert capabilities["methods"][:2] == ["system.health", "system.capabilities"]
    assert "system.bootstrap" in capabilities["methods"]
    assert "research.start" in capabilities["methods"]
    assert "agent.start" in capabilities["methods"]
    assert "run.cancel" in capabilities["methods"]
    assert capabilities["events"] == ["run.event"]
    assert bootstrap["runtime"] == {
        "mode": "local-sidecar",
        "database": "SQLite + JSON",
        "loginRequired": False,
        "networkDefault": True,
    }


def test_real_sidecar_process_keeps_stdout_as_one_json_frame_per_response() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "aegisrun.sidecar"],
        input=_request("system.health") + _request("system.capabilities", request_id="req-2"),
        check=False,
        capture_output=True,
    )

    frames = [json.loads(line) for line in process.stdout.splitlines()]
    assert process.returncode == 0
    assert process.stderr == b""
    assert [frame["id"] for frame in frames] == ["req-1", "req-2"]
    assert frames[0]["result"]["status"] == "ok"
    assert frames[1]["result"]["protocolVersion"] == "1.0"


def test_real_sidecar_reports_unknown_method_without_traceback_or_crash() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "aegisrun.sidecar"],
        input=_request("unknown.method"),
        check=False,
        capture_output=True,
    )

    response = json.loads(process.stdout)
    assert process.returncode == 0
    assert response["error"]["code"] == -32601
    assert "Traceback" not in process.stdout.decode()


def test_sidecar_self_test_is_machine_readable() -> None:
    process = subprocess.run(
        [sys.executable, "-m", "aegisrun.sidecar", "--self-test"],
        check=False,
        capture_output=True,
    )

    assert json.loads(process.stdout)["status"] == "ok"
    assert process.stderr == b""
