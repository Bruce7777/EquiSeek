import { EventEmitter } from 'node:events';
import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import path from 'node:path';
import { app } from 'electron';

type PendingRequest = {
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
};

export class SidecarSupervisor extends EventEmitter {
  private process: ChildProcessWithoutNullStreams | null = null;
  private pending = new Map<string, PendingRequest>();
  private buffer = '';
  private requestSequence = 0;
  private stopping = false;

  async start(): Promise<void> {
    if (this.process && !this.process.killed) return;
    this.stopping = false;
    const repoRoot = process.env.EQUISEEK_REPO_ROOT || process.env.AEGISRUN_REPO_ROOT || path.resolve(app.getAppPath(), '../..');
    const packagedSidecar = process.platform === 'win32'
      ? 'equiseek-sidecar.exe'
      : 'equiseek-sidecar';
    const executable = app.isPackaged
      ? path.join(process.resourcesPath, packagedSidecar)
      : process.env.EQUISEEK_PYTHON || process.env.AEGISRUN_PYTHON || 'python3';
    const args = app.isPackaged ? [] : ['-m', 'aegisrun.sidecar'];
    this.process = spawn(executable, args, {
      cwd: app.isPackaged ? app.getPath('userData') : repoRoot,
      env: {
        PATH: process.env.PATH,
        HOME: process.env.HOME,
        USERPROFILE: process.env.USERPROFILE,
        PYTHONPATH: app.isPackaged ? undefined : path.join(repoRoot, 'src'),
        EQUISEEK_USER_DATA_ROOT: process.env.EQUISEEK_USER_DATA_ROOT,
        AEGISRUN_USER_DATA_ROOT: process.env.AEGISRUN_USER_DATA_ROOT,
      },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    this.process.stdout.setEncoding('utf8');
    this.process.stdout.on('data', (chunk: string) => this.accept(chunk));
    this.process.stderr.setEncoding('utf8');
    this.process.stderr.on('data', (chunk: string) => {
      const safe = chunk.replace(/(?:sk-|token=|api[_-]?key=)[^\s]+/gi, '[redacted]');
      console.error(`[sidecar] ${safe.trim()}`);
    });
    this.process.once('exit', (code, signal) => {
      this.process = null;
      for (const request of this.pending.values()) {
        clearTimeout(request.timer);
        request.reject(new Error(`本地研究服务已中断（${code ?? signal ?? 'unknown'}）`));
      }
      this.pending.clear();
      if (!this.stopping) this.emit('interrupted');
    });
    await this.request('system.health', {}, 8_000);
  }

  async request(
    method: string,
    params: Record<string, unknown> = {},
    timeoutMs = 30_000,
  ): Promise<Record<string, unknown>> {
    if (!this.process) await this.start();
    const processRef = this.process;
    if (!processRef) throw new Error('本地研究服务不可用');
    const id = `desktop-${++this.requestSequence}`;
    const frame = JSON.stringify({
      jsonrpc: '2.0',
      protocolVersion: '1.0',
      id,
      method,
      params,
    });
    if (Buffer.byteLength(frame, 'utf8') > 1024 * 1024) throw new Error('请求超过 1 MiB 限制');
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`${method} 请求超时`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      processRef.stdin.write(`${frame}\n`, 'utf8');
    });
  }

  async stop(): Promise<void> {
    this.stopping = true;
    if (!this.process) return;
    const processRef = this.process;
    try {
      await this.request('system.shutdown', {}, 2_000);
    } catch {
      if (!processRef.killed) processRef.kill();
    }
    if (this.process === processRef) this.process = null;
  }

  private accept(chunk: string): void {
    this.buffer += chunk;
    while (true) {
      const end = this.buffer.indexOf('\n');
      if (end < 0) return;
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + 1);
      if (Buffer.byteLength(line, 'utf8') > 8 * 1024 * 1024) {
        this.process?.kill();
        return;
      }
      let frame: Record<string, unknown>;
      try {
        frame = JSON.parse(line) as Record<string, unknown>;
      } catch {
        this.process?.kill();
        return;
      }
      if (frame.method === 'run.event' && typeof frame.params === 'object' && frame.params) {
        this.emit('run.event', frame.params);
        continue;
      }
      const id = String(frame.id ?? '');
      const pending = this.pending.get(id);
      if (!pending) continue;
      clearTimeout(pending.timer);
      this.pending.delete(id);
      if (typeof frame.error === 'object' && frame.error) {
        const error = frame.error as { message?: string };
        pending.reject(new Error(error.message || '本地研究服务请求失败'));
      } else {
        pending.resolve((frame.result as Record<string, unknown>) || {});
      }
    }
  }
}
