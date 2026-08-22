import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { lstat } from 'node:fs/promises';
import { app, BrowserWindow, dialog, ipcMain, session, shell } from 'electron';
import { SidecarSupervisor } from './sidecar';
import { CredentialStore, type CredentialName } from './credentials';

declare const MAIN_WINDOW_VITE_DEV_SERVER_URL: string | undefined;
declare const MAIN_WINDOW_VITE_NAME: string;

const sidecar = new SidecarSupervisor();
const credentialStore = new CredentialStore();
const attachmentGrants = new Map<string, { path: string; expiresAt: number }>();
let mainWindow: BrowserWindow | null = null;

app.enableSandbox();
if (!app.requestSingleInstanceLock()) app.quit();

function trustedSender(event: Electron.IpcMainInvokeEvent): boolean {
  return Boolean(mainWindow && event.sender === mainWindow.webContents && !event.sender.isDestroyed());
}

function handle(channel: string, method: string, timeout?: number): void {
  ipcMain.handle(channel, async (event, params: Record<string, unknown> = {}) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    if (!params || typeof params !== 'object' || Array.isArray(params)) {
      throw new Error('IPC params must be an object');
    }
    return sidecar.request(method, params, timeout);
  });
}

function registerIpc(): void {
  ipcMain.handle('aegisrun:system:bootstrap', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const result = await sidecar.request('system.bootstrap', {});
    return { ...result, credentials: await credentialStore.status() };
  });
  handle('aegisrun:system:health', 'system.health');
  handle('aegisrun:settings:patch', 'settings.patch');
  handle('aegisrun:workspace:list', 'workspace.list');
  handle('aegisrun:workspace:add', 'workspace.add');
  handle('aegisrun:workspace:select', 'workspace.select');
  handle('aegisrun:research:start', 'research.start');
  ipcMain.handle('aegisrun:agent:start', async (event, input: Record<string, unknown> = {}) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('IPC params must be an object');
    const raw = Array.isArray(input.attachments) ? input.attachments : [];
    const attachments = raw.map((item) => {
      const token = typeof item === 'object' && item ? String((item as { token?: unknown }).token || '') : '';
      const grant = attachmentGrants.get(token);
      if (!grant || grant.expiresAt < Date.now()) throw new Error('附件授权已失效，请重新选择');
      attachmentGrants.delete(token);
      return { path: grant.path };
    });
    return sidecar.request('agent.start', {
      ...input,
      attachments,
      deepseekApiKey: await credentialStore.get(input.modelProvider === 'openai-compatible' ? 'custom' : 'deepseek'),
    });
  });
  handle('aegisrun:macro:start', 'macro.start', 120_000);
  handle('aegisrun:run:get', 'run.get');
  handle('aegisrun:run:list-recent', 'run.list_recent');
  handle('aegisrun:run:events', 'run.events');
  handle('aegisrun:run:cancel', 'run.cancel');
  handle('aegisrun:run:delete', 'run.delete');
  handle('aegisrun:conversation:list', 'conversation.list');
  handle('aegisrun:conversation:create', 'conversation.create');
  handle('aegisrun:conversation:get', 'conversation.get');
  handle('aegisrun:conversation:delete', 'conversation.delete');
  handle('aegisrun:skills:list', 'skills.list');
  handle('aegisrun:skills:get', 'skills.get');
  handle('aegisrun:skills:save', 'skills.save');
  handle('aegisrun:skills:delete', 'skills.delete');
  handle('aegisrun:portfolio:get', 'portfolio.get');
  handle('aegisrun:portfolio:upsert-position', 'portfolio.upsert_position');
  handle('aegisrun:portfolio:remove-position', 'portfolio.remove_position');
  handle('aegisrun:portfolio:upsert-watch', 'portfolio.upsert_watch');
  handle('aegisrun:portfolio:remove-watch', 'portfolio.remove_watch');
  ipcMain.handle('aegisrun:credentials:status', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    return credentialStore.status();
  });
  ipcMain.handle('aegisrun:credentials:set', async (event, input: { name?: unknown; value?: unknown }) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const name = String(input?.name || '') as CredentialName;
    if (!['deepseek', 'custom'].includes(name)) throw new Error('未知凭据类型');
    await credentialStore.set(name, String(input?.value || ''));
    return credentialStore.status();
  });
  ipcMain.handle('aegisrun:credentials:clear', async (event, input: { name?: unknown }) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const name = String(input?.name || '') as CredentialName;
    if (!['deepseek', 'custom'].includes(name)) throw new Error('未知凭据类型');
    await credentialStore.clear(name);
    return credentialStore.status();
  });
  ipcMain.handle('aegisrun:skills:import-file', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const result = await dialog.showOpenDialog(mainWindow!, { title: '导入 SKILL.md', properties: ['openFile'], filters: [{ name: 'Skill Markdown', extensions: ['md'] }] });
    if (result.canceled || !result.filePaths[0]) return null;
    return sidecar.request('skills.import_file', { path: result.filePaths[0] });
  });
  ipcMain.handle('aegisrun:skills:open-root', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const result = await sidecar.request('skills.root', {}) as { path?: unknown };
    return shell.openPath(String(result.path || ''));
  });
  ipcMain.handle('aegisrun:native:choose-directory', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const result = await dialog.showOpenDialog(mainWindow!, {
      properties: ['openDirectory', 'createDirectory'],
      title: '选择求衡工作区',
    });
    return result.canceled ? null : result.filePaths[0] || null;
  });
  ipcMain.handle('aegisrun:native:choose-attachments', async (event) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: '添加研究上下文', properties: ['openFile', 'multiSelections'],
      filters: [{ name: '研究文件', extensions: ['txt', 'md', 'csv', 'json', 'pdf', 'png', 'jpg', 'jpeg', 'webp'] }],
    });
    if (result.canceled) return [];
    const selected = [];
    for (const selectedPath of result.filePaths.slice(0, 4)) {
      const metadata = await lstat(selectedPath);
      if (!metadata.isFile() || metadata.isSymbolicLink()) continue;
      const token = randomUUID();
      attachmentGrants.set(token, { path: selectedPath, expiresAt: Date.now() + 10 * 60_000 });
      selected.push({ token, name: path.basename(selectedPath), mimeType: '', sizeBytes: metadata.size });
    }
    return selected;
  });
  ipcMain.handle('aegisrun:native:open-path', async (event, input: { path?: unknown }) => {
    if (!trustedSender(event)) throw new Error('IPC sender rejected');
    const target = typeof input?.path === 'string' ? input.path : '';
    if (!path.isAbsolute(target) || !['.html', '.md', '.json', '.txt'].includes(path.extname(target))) {
      throw new Error('Only absolute local research artifact paths are allowed');
    }
    const metadata = await lstat(target);
    if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error('Artifact is not a regular file');
    return shell.openPath(target);
  });
}

async function createWindow(): Promise<void> {
  await sidecar.start();
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 960,
    minWidth: 1120,
    minHeight: 720,
    title: 'EquiSeek 求衡',
    backgroundColor: '#f2f1ed',
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      spellcheck: false,
    },
  });
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    const parsed = new URL(url);
    if (parsed.protocol === 'https:') {
      void shell.openExternal(url);
    }
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    const current = mainWindow?.webContents.getURL();
    if (current && url !== current) event.preventDefault();
  });
  sidecar.on('run.event', (event) => mainWindow?.webContents.send('aegisrun:run:event', event));
  sidecar.on('interrupted', () => mainWindow?.webContents.send('aegisrun:sidecar:interrupted'));
  mainWindow.once('ready-to-show', () => mainWindow?.show());
  if (MAIN_WINDOW_VITE_DEV_SERVER_URL) {
    await mainWindow.loadURL(MAIN_WINDOW_VITE_DEV_SERVER_URL);
  } else {
    await mainWindow.loadFile(
      path.join(__dirname, `../renderer/${MAIN_WINDOW_VITE_NAME}/index.html`),
    );
  }
}

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false);
  });
  registerIpc();
  await createWindow();
});

app.on('second-instance', () => {
  if (mainWindow?.isMinimized()) mainWindow.restore();
  mainWindow?.focus();
});
app.on('window-all-closed', () => app.quit());
app.on('before-quit', () => void sidecar.stop());
