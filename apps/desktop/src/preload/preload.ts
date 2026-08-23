import { contextBridge, ipcRenderer } from 'electron';
import type { DesktopApi, RunEvent } from '../shared/contracts';

const invoke = <T>(channel: string, params: Record<string, unknown> = {}): Promise<T> =>
  ipcRenderer.invoke(channel, params) as Promise<T>;

const api: DesktopApi = {
  system: {
    bootstrap: () => invoke('aegisrun:system:bootstrap'),
    health: () => invoke('aegisrun:system:health'),
  },
  settings: { patch: (input) => invoke('aegisrun:settings:patch', input) },
  workspaces: {
    list: () => invoke('aegisrun:workspace:list'),
    add: (path) => invoke('aegisrun:workspace:add', { path }),
    select: (workspaceId) => invoke('aegisrun:workspace:select', { workspaceId }),
  },
  credentials: {
    status: () => invoke('aegisrun:credentials:status'),
    set: (name, value) => invoke('aegisrun:credentials:set', { name, value }),
    clear: (name) => invoke('aegisrun:credentials:clear', { name }),
  },
  skills: {
    list: () => invoke('aegisrun:skills:list'),
    get: (name) => invoke('aegisrun:skills:get', { name }),
    save: (name, content) => invoke('aegisrun:skills:save', { name, content }),
    delete: (name) => invoke('aegisrun:skills:delete', { name }),
    importFile: () => invoke('aegisrun:skills:import-file'),
    openRoot: () => invoke('aegisrun:skills:open-root'),
  },
  research: {
    start: (input) => invoke('aegisrun:research:start', input),
    history: (input = {}) => invoke('aegisrun:research:history', input),
  },
  agent: { start: (input) => invoke('aegisrun:agent:start', input) },
  macro: { start: () => invoke('aegisrun:macro:start') },
  runs: {
    get: (runId) => invoke('aegisrun:run:get', { runId }),
    listRecent: () => invoke('aegisrun:run:list-recent'),
    events: (runId, afterSeq = 0) => invoke('aegisrun:run:events', { runId, afterSeq }),
    cancel: (runId) => invoke('aegisrun:run:cancel', { runId }),
    delete: (runId) => invoke('aegisrun:run:delete', { runId }),
    subscribe: (listener) => {
      const handler = (_event: Electron.IpcRendererEvent, payload: RunEvent) => listener(payload);
      ipcRenderer.on('aegisrun:run:event', handler);
      return () => ipcRenderer.removeListener('aegisrun:run:event', handler);
    },
  },
  conversations: {
    list: () => invoke('aegisrun:conversation:list'),
    create: () => invoke('aegisrun:conversation:create'),
    get: (threadId) => invoke('aegisrun:conversation:get', { threadId }),
    delete: (threadId) => invoke('aegisrun:conversation:delete', { threadId }),
  },
  portfolio: {
    get: () => invoke('aegisrun:portfolio:get'),
    upsertPosition: (input) => invoke('aegisrun:portfolio:upsert-position', input),
    removePosition: (symbol) => invoke('aegisrun:portfolio:remove-position', { symbol }),
    upsertWatch: (input) => invoke('aegisrun:portfolio:upsert-watch', input),
    removeWatch: (symbol) => invoke('aegisrun:portfolio:remove-watch', { symbol }),
  },
  native: {
    chooseDirectory: () => ipcRenderer.invoke('aegisrun:native:choose-directory') as Promise<string | null>,
    chooseAttachments: () => ipcRenderer.invoke('aegisrun:native:choose-attachments'),
    openPath: (path) => ipcRenderer.invoke('aegisrun:native:open-path', { path }) as Promise<string>,
  },
};

contextBridge.exposeInMainWorld('aegisrun', api);
