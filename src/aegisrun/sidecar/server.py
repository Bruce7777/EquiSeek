from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from typing import BinaryIO

from aegisrun.sidecar.dispatcher import SidecarDispatcher
from aegisrun.sidecar.protocol import (
    MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    RpcProtocolError,
    encode_message,
    error_response,
    parse_request,
    success_response,
)


def _write(stream: BinaryIO, value: dict[str, object]) -> None:
    stream.write(encode_message(value))
    stream.flush()


def _drain_oversized_frame(stream: BinaryIO) -> None:
    while True:
        chunk = stream.readline(MAX_FRAME_BYTES + 2)
        if not chunk or chunk.endswith(b"\n"):
            return


async def _serve_async(input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    write_lock = asyncio.Lock()

    async def write(value: dict[str, object]) -> None:
        async with write_lock:
            _write(output_stream, value)

    async def notify(event: dict[str, object]) -> None:
        await write(
            {
                "jsonrpc": "2.0",
                "method": "run.event",
                "params": event,
            }
        )

    dispatcher = SidecarDispatcher()
    dispatcher.runs.set_notifier(notify)
    while True:
        frame = await asyncio.to_thread(input_stream.readline, MAX_FRAME_BYTES + 2)
        if not frame:
            await dispatcher.runs.shutdown()
            return 0
        if len(frame) > MAX_FRAME_BYTES:
            if not frame.endswith(b"\n"):
                await asyncio.to_thread(_drain_oversized_frame, input_stream)
            await write(error_response(RpcProtocolError(-32600, "frame exceeds 1 MiB")))
            continue
        try:
            request = parse_request(frame)
            result = await dispatcher.dispatch(request)
            response = success_response(request.request_id, result)
        except RpcProtocolError as error:
            response = error_response(error)
        except Exception as error:  # defensive boundary: never leak a traceback over stdout
            response = error_response(RpcProtocolError(-32000, type(error).__name__))
        await write(response)
        if dispatcher.shutdown_requested:
            return 0


def serve(input_stream: BinaryIO, output_stream: BinaryIO) -> int:
    return asyncio.run(_serve_async(input_stream, output_stream))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EquiSeek local sidecar")
    parser.add_argument("--self-test", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    if options.self_test:
        _write(
            sys.stdout.buffer,
            {
                "status": "ok",
                "protocolVersion": PROTOCOL_VERSION,
                "transport": "stdio-ndjson",
            },
        )
        return 0
    return serve(sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    raise SystemExit(main())
