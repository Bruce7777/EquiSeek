import path from 'node:path';
import { chmod, readFile, writeFile, mkdir } from 'node:fs/promises';
import { app, safeStorage } from 'electron';

type CredentialName = 'deepseek' | 'custom';
type StoredCredentials = Partial<Record<CredentialName, string>>;

export class CredentialStore {
  private filePath(): string {
    return path.join(app.getPath('userData'), 'credentials.json');
  }

  private async load(): Promise<StoredCredentials> {
    try {
      return JSON.parse(await readFile(this.filePath(), 'utf8')) as StoredCredentials;
    } catch {
      return {};
    }
  }

  async status(): Promise<Record<CredentialName, boolean>> {
    const stored = await this.load();
    return {
      deepseek: Boolean(stored.deepseek),
      custom: Boolean(stored.custom),
    };
  }

  async get(name: CredentialName): Promise<string | undefined> {
    const encrypted = (await this.load())[name];
    if (!encrypted || !safeStorage.isEncryptionAvailable()) return undefined;
    return safeStorage.decryptString(Buffer.from(encrypted, 'base64'));
  }

  async set(name: CredentialName, value: string): Promise<void> {
    const clean = value.trim();
    if (!clean || clean.length > 512) throw new Error('API Key 格式无效');
    if (!safeStorage.isEncryptionAvailable()) throw new Error('系统安全存储当前不可用');
    const stored = await this.load();
    stored[name] = safeStorage.encryptString(clean).toString('base64');
    await mkdir(path.dirname(this.filePath()), { recursive: true });
    await writeFile(this.filePath(), `${JSON.stringify(stored, null, 2)}\n`, { mode: 0o600 });
    await chmod(this.filePath(), 0o600);
  }

  async clear(name: CredentialName): Promise<void> {
    const stored = await this.load();
    delete stored[name];
    await mkdir(path.dirname(this.filePath()), { recursive: true });
    await writeFile(this.filePath(), `${JSON.stringify(stored, null, 2)}\n`, { mode: 0o600 });
    await chmod(this.filePath(), 0o600);
  }
}

export type { CredentialName };
