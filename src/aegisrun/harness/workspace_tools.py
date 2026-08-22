from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shlex
import signal
import sys
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from aegisrun.core.errors import SandboxViolationError
from aegisrun.core.security import safe_join

_PROTECTED_ROOTS = {".equiseek", ".aegisrun"}
_MAX_TEXT_BYTES = 512 * 1024
_MAX_WRITE_BYTES = 256 * 1024


def _version(path: Path, data: bytes) -> tuple[int, int, str]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size, hashlib.sha256(data).hexdigest()


class WorkspaceFileEditor:
    """DeepSeek-Harness-inspired read/write/edit tools scoped to one workspace.

    Existing files must be observed with ``read`` before mutation and must remain
    unchanged between the read and the write/edit. This prevents a model from
    blindly overwriting user work or racing an external editor.
    """

    def __init__(self, root: Path, *, writable: bool) -> None:
        self.root = root.resolve(strict=True)
        self.writable = writable
        self._observed: dict[Path, tuple[int, int, str]] = {}

    def _path(self, value: object, *, must_exist: bool = False) -> Path:
        relative = str(value or "").strip()
        if not relative or relative in {".", ".."}:
            raise SandboxViolationError("请提供工作区内的相对路径")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise SandboxViolationError(f"路径超出工作区：{relative}")
        if candidate.parts and candidate.parts[0] in _PROTECTED_ROOTS:
            raise SandboxViolationError("运行时内部目录不允许通过文件工具访问")
        current = self.root
        for part in candidate.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise SandboxViolationError(f"工作区路径不允许符号链接：{relative}")
        target = safe_join(self.root, relative, must_exist=must_exist)
        return target

    def list(self, relative: object = ".", *, recursive: bool = False) -> dict[str, object]:
        base_value = str(relative or ".").strip() or "."
        base = self.root if base_value == "." else self._path(base_value, must_exist=True)
        if not base.is_dir():
            raise ValueError(f"不是目录：{base_value}")
        iterator = base.rglob("*") if recursive else base.iterdir()
        items: list[dict[str, object]] = []
        for item in iterator:
            try:
                relative_path = item.relative_to(self.root)
                if relative_path.parts and relative_path.parts[0] in _PROTECTED_ROOTS:
                    continue
                if item.is_symlink():
                    continue
                items.append(
                    {
                        "path": relative_path.as_posix(),
                        "kind": "directory" if item.is_dir() else "file",
                        "size_bytes": item.stat().st_size if item.is_file() else 0,
                    }
                )
            except OSError:
                continue
            if len(items) >= 200:
                break
        return {"root": str(self.root), "items": items, "truncated": len(items) >= 200}

    def read(self, relative: object, *, offset: int = 1, limit: int = 400) -> dict[str, object]:
        path = self._path(relative, must_exist=True)
        if not path.is_file():
            raise ValueError(f"不是普通文件：{relative}")
        data = path.read_bytes()
        if len(data) > _MAX_TEXT_BYTES:
            raise ValueError("文件超过 512 KiB 单次阅读上限")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("文件不是 UTF-8 文本") from error
        lines = content.splitlines()
        start = max(1, min(int(offset), max(len(lines), 1)))
        count = max(1, min(int(limit), 1_000))
        selected = [
            {"number": index + 1, "text": lines[index]}
            for index in range(start - 1, min(start - 1 + count, len(lines)))
        ]
        self._observed[path] = _version(path, data)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "offset": start,
            "lines": selected,
            "total_lines": len(lines),
            "truncated": start - 1 + count < len(lines),
        }

    def _require_writable(self) -> None:
        if not self.writable:
            raise SandboxViolationError("当前工作区权限为只读，请先在 Agent 入口切换为“可编辑”")

    def _require_fresh_observation(self, path: Path) -> None:
        observed = self._observed.get(path)
        if observed is None:
            raise SandboxViolationError("修改已有文件前必须先调用 read 阅读该文件")
        data = path.read_bytes()
        if _version(path, data) != observed:
            raise SandboxViolationError("文件在读取后已被其他程序修改，请重新 read 后再操作")

    def _atomic_write(self, path: Path, content: str) -> None:
        data = content.encode("utf-8")
        if len(data) > _MAX_WRITE_BYTES:
            raise ValueError("单次写入不能超过 256 KiB")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        self._observed[path] = _version(path, data)

    def write(self, relative: object, content: object) -> dict[str, object]:
        self._require_writable()
        path = self._path(relative)
        existed = path.exists()
        if existed:
            if not path.is_file():
                raise ValueError(f"不是普通文件：{relative}")
            self._require_fresh_observation(path)
        value = str(content)
        self._atomic_write(path, value)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "operation": "update" if existed else "create",
            "size_bytes": len(value.encode("utf-8")),
        }

    def edit(
        self,
        relative: object,
        old_string: object,
        new_string: object,
        *,
        replace_all: bool = False,
    ) -> dict[str, object]:
        self._require_writable()
        path = self._path(relative, must_exist=True)
        if not path.is_file():
            raise ValueError(f"不是普通文件：{relative}")
        self._require_fresh_observation(path)
        old = str(old_string)
        if not old:
            raise ValueError("old_string 不能为空")
        content = path.read_text(encoding="utf-8")
        matches = content.count(old)
        if matches == 0:
            raise ValueError("old_string 在文件中不存在")
        if matches > 1 and not replace_all:
            raise ValueError(f"old_string 匹配 {matches} 处，请扩大上下文或显式启用 replace_all")
        updated = content.replace(old, str(new_string), -1 if replace_all else 1)
        self._atomic_write(path, updated)
        return {
            "path": path.relative_to(self.root).as_posix(),
            "replacements": matches if replace_all else 1,
            "size_bytes": len(updated.encode("utf-8")),
        }


@dataclass(frozen=True, slots=True)
class ShellCommandResult:
    command: str
    output: str
    exit_code: int
    cwd: str
    truncated: bool
    sandbox: str
    reset: bool = False
    timed_out: bool = False


class PersistentWorkspaceShell:
    """One owner-scoped persistent Bash per Agent run.

    The command protocol follows DeepSeek Harness' persistent Bash tool: calls
    are serialized, private start/end markers isolate each result, cwd and
    environment survive successful calls, and uncertain sessions reset.
    """

    def __init__(self, root: Path, *, writable: bool, network_allowed: bool) -> None:
        self.root = root.resolve(strict=True)
        self.writable = writable
        self.network_allowed = network_allowed
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._cwd = str(self.root)

    def _home_read_denials(self) -> list[str]:
        """Keep the shell away from user files outside the selected workspace.

        macOS command-line programs require broad access to signed system resources.
        Seatbelt deny rules therefore cover every sibling branch from the user's home
        directory down to the selected workspace, while system paths remain readable.
        """
        home = Path.home().resolve()
        if self.root != home and home not in self.root.parents:
            return [f"(deny file-read* (subpath {json.dumps(str(home))}))"]
        denials: list[str] = []
        keep = self.root
        current = home
        while current != self.root:
            next_part = keep.relative_to(current).parts[0]
            try:
                siblings = tuple(current.iterdir())
            except OSError:
                siblings = ()
            for sibling in siblings:
                if sibling.name == next_part:
                    continue
                operation = "subpath" if sibling.is_dir() else "literal"
                denials.append(
                    f"(deny file-read* ({operation} {json.dumps(str(sibling))}))"
                )
            current /= next_part
        return denials

    def _profile(self) -> str:
        quoted_root = json.dumps(str(self.root))
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow signal (target self))",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow file-read*)",
        ]
        rules.extend(self._home_read_denials())
        if self.writable:
            rules.append(f"(allow file-write* (subpath {quoted_root}))")
        if self.network_allowed:
            rules.append("(allow network*)")
        return "\n".join(rules)

    async def _start(self) -> asyncio.subprocess.Process:
        if self._process is not None and self._process.returncode is None:
            return self._process
        sandbox_available = await asyncio.to_thread(Path("/usr/bin/sandbox-exec").is_file)
        if sys.platform != "darwin" or not sandbox_available:
            raise SandboxViolationError("当前系统没有可用的 macOS Workspace Sandbox，未启动 Shell")
        environment = {
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.root),
            "TMPDIR": str(self.root),
            "LANG": os.getenv("LANG", "en_US.UTF-8"),
            "EQUISEEK_WORKSPACE": str(self.root),
        }
        self._process = await asyncio.create_subprocess_exec(
            "/usr/bin/sandbox-exec",
            "-p",
            self._profile(),
            "/bin/bash",
            "--noprofile",
            "--norc",
            cwd=self.root,
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        return self._process

    async def run(
        self,
        command: object,
        *,
        timeout_seconds: int = 30,
        max_output_bytes: int = 64_000,
    ) -> ShellCommandResult:
        value = str(command or "").strip()
        if not value or len(value) > 8_000 or "\x00" in value:
            raise ValueError("Shell 命令为空或超过 8,000 字符上限")
        timeout_seconds = max(1, min(int(timeout_seconds), 120))
        async with self._lock:
            process = await self._start()
            assert process.stdin is not None and process.stdout is not None
            stdout = process.stdout
            nonce = uuid4().hex
            start_marker = f"__EQUISEEK_PERSISTENT_BASH_START_{nonce}__"
            end_marker = f"__EQUISEEK_PERSISTENT_BASH_END_{nonce}:"
            quoted_command = shlex.quote(value)
            payload = (
                f"printf '%s\\n' {shlex.quote(start_marker)}; "
                f"eval -- {quoted_command}; "
                "__aegisrun_status=$?; "
                f"printf '%s%s:%s\\n' {shlex.quote(end_marker)} "
                '"$__aegisrun_status" "$PWD"\n'
            )
            process.stdin.write(payload.encode("utf-8"))
            await process.stdin.drain()
            output = bytearray()
            truncated = False
            exit_code: int | None = None
            started = False

            async def collect() -> None:
                nonlocal truncated, exit_code, started
                marker_pattern = re.compile(rf"{re.escape(end_marker)}(-?\d+):(.*)$")
                while True:
                    line = await stdout.readline()
                    if not line:
                        return
                    decoded = line.decode("utf-8", errors="replace")
                    match = marker_pattern.search(decoded.rstrip("\r\n"))
                    if match:
                        prefix = decoded[: match.start()]
                        if started and prefix:
                            remaining = max_output_bytes - len(output)
                            encoded_prefix = prefix.encode("utf-8")
                            if remaining > 0:
                                output.extend(encoded_prefix[:remaining])
                            if len(encoded_prefix) > max(remaining, 0):
                                truncated = True
                        exit_code = int(match.group(1))
                        self._cwd = match.group(2)
                        return
                    if decoded.strip() == start_marker:
                        started = True
                        continue
                    if not started:
                        continue
                    remaining = max_output_bytes - len(output)
                    if remaining > 0:
                        output.extend(line[:remaining])
                    if len(line) > max(remaining, 0):
                        truncated = True

            try:
                await asyncio.wait_for(collect(), timeout_seconds)
            except TimeoutError:
                await self.close()
                message = (
                    output.decode("utf-8", errors="replace").rstrip()
                    + f"\n[timeout: {timeout_seconds}s; persistent shell reset]"
                ).lstrip()
                return ShellCommandResult(
                    command=value,
                    output=message,
                    exit_code=124,
                    cwd=str(self.root),
                    truncated=truncated,
                    sandbox=(
                        "macos-seatbelt-workspace-write"
                        if self.writable
                        else "macos-seatbelt-read-only"
                    ),
                    reset=True,
                    timed_out=True,
                )
            reset = exit_code is None
            if reset:
                return_code = await process.wait()
                await self.close()
                exit_code = return_code
                suffix = f"[shell exited: code {exit_code}; persistent shell reset]"
                if output:
                    output.extend(f"\n{suffix}".encode())
                else:
                    output.extend(suffix.encode())
            return ShellCommandResult(
                command=value,
                output=output.decode("utf-8", errors="replace").rstrip(),
                exit_code=exit_code if exit_code is not None else 1,
                cwd=self._cwd,
                truncated=truncated,
                sandbox=(
                    "macos-seatbelt-workspace-write"
                    if self.writable
                    else "macos-seatbelt-read-only"
                ),
                reset=reset,
            )

    async def close(self) -> None:
        process, self._process = self._process, None
        self._cwd = str(self.root)
        if process is None or process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), 2)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
