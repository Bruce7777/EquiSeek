"""Versioned stdio protocol for the local Electron sidecar."""

from aegisrun.sidecar.protocol import PROTOCOL_VERSION, RpcRequest, encode_message, parse_request

__all__ = ["PROTOCOL_VERSION", "RpcRequest", "encode_message", "parse_request"]
