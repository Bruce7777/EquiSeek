export type RunStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';

export interface RunEvent {
  runId: string;
  seq: number;
  type: string;
  at: string;
  payload: Record<string, unknown>;
}

export interface RunView {
  runId: string;
  kind: string;
  status: RunStatus;
  createdAt: string;
  lastSeq: number;
  result?: Record<string, unknown> | null;
  error?: { code: string; message: string; retryable: boolean } | null;
}

export interface SkillSummary {
  name: string;
  description: string;
  provider: string;
  version: string;
  sourceLabel: string;
  model_invocable: boolean;
  user_invocable: boolean;
}

export interface PortfolioBook {
  schema_version: number;
  positions: Array<Record<string, unknown>>;
  watchlist: Array<Record<string, unknown>>;
}

export interface ConversationSummary {
  threadId: string;
  title: string;
  preview: string;
  turnCount: number;
  updatedAt: string;
}

export interface ConversationTurn {
  role: 'user' | 'assistant';
  content: string;
  intent?: string | null;
  runId?: string | null;
  attachments?: AttachmentSummary[];
}

export interface AttachmentSummary { name: string; mimeType: string; sizeBytes: number }
export interface AttachmentSelection extends AttachmentSummary { token: string }
export interface WorkspaceSummary { id: string; name: string; path: string; active: boolean; writable: boolean }
export interface CredentialStatus { deepseek: boolean; custom: boolean; tushare: boolean }

export interface SkillDetail extends SkillSummary {
  content: string;
  editable: boolean;
}

export interface ConversationState {
  threadId: string;
  turns: ConversationTurn[];
  summary: string;
  compressedTurnCount: number;
  updatedAt: string;
}

export interface BootstrapData {
  settings: Record<string, unknown>;
  workspaces: WorkspaceSummary[];
  skills: SkillSummary[];
  portfolio: PortfolioBook;
  recentRuns: RunView[];
  conversations: ConversationSummary[];
  runtime: { mode: string; database: string; loginRequired: boolean; networkDefault: boolean };
  credentials?: CredentialStatus;
}

export interface DesktopApi {
  system: {
    bootstrap(): Promise<BootstrapData>;
    health(): Promise<Record<string, unknown>>;
  };
  settings: { patch(input: Record<string, unknown>): Promise<Record<string, unknown>> };
  workspaces: {
    list(): Promise<{ items: WorkspaceSummary[] }>;
    add(path: string): Promise<{ items: WorkspaceSummary[]; activeId: string }>;
    select(workspaceId: string): Promise<{ items: WorkspaceSummary[]; activeId: string }>;
  };
  credentials: {
    status(): Promise<CredentialStatus>;
    set(name: 'deepseek' | 'custom' | 'tushare', value: string): Promise<CredentialStatus>;
    clear(name: 'deepseek' | 'custom' | 'tushare'): Promise<CredentialStatus>;
  };
  skills: {
    list(): Promise<{ items: SkillSummary[] }>;
    get(name: string): Promise<SkillDetail>;
    save(name: string, content: string): Promise<SkillDetail>;
    delete(name: string): Promise<{ deleted: string; items: SkillSummary[] }>;
    importFile(): Promise<SkillDetail | null>;
    openRoot(): Promise<string>;
  };
  research: {
    start(input: Record<string, unknown>): Promise<RunView>;
    history(input?: { refresh?: boolean }): Promise<{ items: RunView[]; refreshed: boolean }>;
  };
  agent: { start(input: Record<string, unknown>): Promise<RunView> };
  macro: { start(): Promise<RunView> };
  runs: {
    get(runId: string): Promise<RunView>;
    listRecent(): Promise<{ items: RunView[] }>;
    events(runId: string, afterSeq?: number): Promise<{ items: RunEvent[] }>;
    cancel(runId: string): Promise<RunView>;
    delete(runId: string): Promise<{ deleted: string }>;
    subscribe(listener: (event: RunEvent) => void): () => void;
  };
  conversations: {
    list(): Promise<{ items: ConversationSummary[] }>;
    create(): Promise<ConversationState>;
    get(threadId: string): Promise<ConversationState>;
    delete(threadId: string): Promise<{ deleted: string }>;
  };
  portfolio: {
    get(): Promise<PortfolioBook>;
    upsertPosition(input: Record<string, unknown>): Promise<PortfolioBook>;
    removePosition(symbol: string): Promise<PortfolioBook>;
    upsertWatch(input: Record<string, unknown>): Promise<PortfolioBook>;
    removeWatch(symbol: string): Promise<PortfolioBook>;
  };
  native: {
    chooseDirectory(): Promise<string | null>;
    chooseAttachments(): Promise<AttachmentSelection[]>;
    openPath(path: string): Promise<string>;
  };
}

declare global {
  interface Window {
    aegisrun?: DesktopApi;
  }
}
