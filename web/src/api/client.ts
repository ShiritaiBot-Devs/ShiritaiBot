import type {
  CommandInfo,
  ConfigSaveResult,
  ConfigSnapshot,
  DiagnosticsResult,
  DiscoveredModel,
  FeishuQrAdapter,
  FeishuQrRegistration,
  Health,
  HelpMenuSection,
  HistoryMessage,
  KnowledgeDetail,
  KnowledgeDocument,
  KnowledgeHit,
  KnowledgeLibrary,
  KnowledgeListing,
  LogFeed,
  MemeListing,
  MemeRecord,
  MemoryDetail,
  MemoryProfile,
  MemoryRecord,
  MemoryStatus,
  ModelConfig,
  ModelTestResult,
  McpServerStatus,
  PluginInfo,
  ProviderConfig,
  RecordKind,
  RecordMap,
  RoutineDraft,
  RoutineRecord,
  Settings,
  SkillDetail,
  SkillInfo,
  Statistics,
  ExchangeRate,
  ToolCatalog,
  Paginated,
  CommitPreview,
  ReleaseListing,
  UpdateInspect,
  UpdateStatus,
  WebChat,
} from "./types";

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export interface LogFilter {
  limit: number;
  level: string;
  source: string;
  query: string;
}

export type HttpMethod = "GET" | "POST" | "PUT" | "DELETE";

export interface ApiClient {
  health(): Promise<Health>;
  restart(): Promise<{ status: string; instance_id: string }>;
  updateStatus(): Promise<UpdateStatus>;
  updateReleases(
    page: number,
    size: number,
    prerelease: boolean,
    refresh?: boolean,
  ): Promise<ReleaseListing>;
  updatePreview(refresh?: boolean): Promise<{ commit: CommitPreview | null }>;
  inspectUpdate(
    kind: "release" | "commit",
    ref: string,
  ): Promise<UpdateInspect>;
  applyUpdate(kind: "release" | "commit", ref: string): Promise<UpdateStatus>;
  config(): Promise<Settings>;
  configSnapshot(): Promise<ConfigSnapshot>;
  tools(session?: string): Promise<ToolCatalog>;
  saveConfig(settings: Settings, revision?: string): Promise<ConfigSaveResult>;
  startFeishuQr(
    domain: FeishuQrRegistration["domain"],
  ): Promise<FeishuQrRegistration>;
  feishuQrStatus(id: string): Promise<FeishuQrRegistration>;
  cancelFeishuQr(id: string): Promise<void>;
  applyFeishuQr(
    id: string,
    adapter: FeishuQrAdapter,
  ): Promise<{ adapter_id: string }>;
  diagnostics(): Promise<DiagnosticsResult>;
  records<K extends RecordKind>(kind: K): Promise<RecordMap[K][]>;
  recordsPage<K extends RecordKind>(
    kind: K,
    page: number,
    pageSize: number,
    approvalFilter?: "pending" | "history",
  ): Promise<Paginated<RecordMap[K]>>;
  stats(hours: number, session?: string): Promise<Statistics>;
  statsSessions(): Promise<string[]>;
  exchangeRate(): Promise<ExchangeRate>;
  commands(session: string): Promise<CommandInfo[]>;
  helpMenuSections(): Promise<HelpMenuSection[]>;
  helpMenuImage(id: string): Promise<Blob>;
  history(session: string): Promise<HistoryMessage[]>;
  webChats(): Promise<WebChat[]>;
  updateWebChat(
    session: string,
    patch: Partial<Pick<WebChat, "title" | "pinned">>,
  ): Promise<unknown>;
  deleteWebChat(session: string): Promise<unknown>;
  rawLogs(lines: number, query: string): Promise<{ lines: string[] }>;
  logs(filter: LogFilter): Promise<LogFeed>;
  logLevel(): Promise<{ level: string }>;
  setLogLevel(level: string): Promise<{ level: string }>;
  sendMessage(
    text: string,
    session: string,
    clientId?: string,
  ): Promise<{ status: string }>;
  resolveApproval(id: string, allow: boolean): Promise<unknown>;
  memoryProfiles(): Promise<MemoryProfile[]>;
  memories(
    kind?: string,
    query?: string,
    profile?: string,
  ): Promise<MemoryRecord[]>;
  memoriesPage(
    kind: string,
    query: string,
    page: number,
    pageSize: number,
    profile?: string,
  ): Promise<Paginated<MemoryRecord>>;
  memory(id: string, profile?: string): Promise<MemoryDetail>;
  memoryStatus(profile?: string): Promise<MemoryStatus>;
  memes(
    category: string,
    query: string,
    page: number,
    pageSize: number,
  ): Promise<MemeListing>;
  memeImage(id: string): Promise<Blob>;
  uploadMeme(
    file: File,
    category: string,
    description: string,
  ): Promise<MemeRecord>;
  updateMeme(
    id: string,
    patch: { category?: string; description?: string },
  ): Promise<MemeRecord>;
  describeMeme(id: string): Promise<MemeRecord>;
  deleteMeme(id: string): Promise<unknown>;
  knowledgeLibraries(): Promise<KnowledgeLibrary[]>;
  knowledge(
    query: string,
    page: number,
    pageSize: number,
    library?: string,
  ): Promise<KnowledgeListing>;
  knowledgeDocument(
    id: string,
    start: number,
    count: number,
    library?: string,
  ): Promise<KnowledgeDetail>;
  knowledgeFile(id: string, library?: string): Promise<Blob>;
  knowledgeSearch(query: string, library?: string): Promise<KnowledgeHit[]>;
  uploadKnowledge(file: File, library?: string): Promise<KnowledgeDocument>;
  deleteKnowledge(id: string, library?: string): Promise<unknown>;
  routines(): Promise<RoutineRecord[]>;
  createRoutine(draft: RoutineDraft): Promise<RoutineRecord>;
  setRoutineStatus(id: string, status: string): Promise<RoutineRecord>;
  cancelSubagent(id: string): Promise<unknown>;
  detectSubagent(id: string): Promise<unknown>;
  plugins(): Promise<PluginInfo[]>;
  reloadPlugins(): Promise<PluginInfo[]>;
  reloadPlugin(id: string): Promise<PluginInfo[]>;
  installPluginZip(id: string, file: File): Promise<PluginInfo[]>;
  installPluginGithub(id: string, url: string): Promise<PluginInfo[]>;
  removePlugin(id: string): Promise<PluginInfo[]>;
  skills(): Promise<SkillInfo[]>;
  skill(name: string): Promise<SkillDetail>;
  installSkill(file: File, replace: boolean): Promise<SkillInfo[]>;
  removeSkill(name: string): Promise<SkillInfo[]>;
  mcpServers(): Promise<McpServerStatus[]>;
  reconnectMcp(id: string): Promise<McpServerStatus[]>;
  discoverModels(provider: ProviderConfig): Promise<DiscoveredModel[]>;
  testModel(
    provider: ProviderConfig,
    model: ModelConfig,
  ): Promise<ModelTestResult>;
}

function messageFor(status: number, detail: unknown): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  if (status === 401) return "管理令牌无效或已过期，请重新登录。";
  return `请求失败（HTTP ${status}），请检查后端服务。`;
}

export function createClient(getToken: () => string): ApiClient {
  async function requestSnapshot<T>(
    path: string,
    method: HttpMethod = "GET",
    body?: unknown,
    revision?: string,
  ): Promise<{ data: T; revision?: string }> {
    const headers: Record<string, string> = {
      Authorization: "Bearer " + getToken(),
    };
    if (revision !== undefined) {
      headers["X-Config-Revision"] = revision;
      // Preserve conditional saves when talking to an older backend.
      headers["If-Match"] = revision;
    }
    const binary = body instanceof Blob;
    if (body !== undefined)
      headers["Content-Type"] = binary
        ? body.type || "application/octet-stream"
        : "application/json";
    let response: Response;
    try {
      response = await fetch("/api" + path, {
        method,
        headers,
        body:
          body === undefined ? undefined : binary ? body : JSON.stringify(body),
      });
    } catch {
      throw new ApiError("无法连接后端服务，请确认进程仍在运行。", 0);
    }
    if (response.ok) {
      return {
        data: (response.status === 204
          ? undefined
          : await response.json()) as T,
        revision:
          response.headers?.get("X-Config-Revision") ??
          response.headers?.get("ETag") ??
          undefined,
      };
    }
    const payload = (await response.json().catch(() => null)) as {
      detail?: unknown;
    } | null;
    throw new ApiError(
      messageFor(response.status, payload?.detail),
      response.status,
    );
  }

  async function request<T>(
    path: string,
    method?: HttpMethod,
    body?: unknown,
    revision?: string,
  ): Promise<T> {
    return (await requestSnapshot<T>(path, method, body, revision)).data;
  }

  async function requestBlob(path: string): Promise<Blob> {
    let response: Response;
    try {
      response = await fetch("/api" + path, {
        headers: { Authorization: "Bearer " + getToken() },
      });
    } catch {
      throw new ApiError("无法连接后端服务，请确认进程仍在运行。", 0);
    }
    if (!response.ok)
      throw new ApiError(messageFor(response.status, null), response.status);
    return response.blob();
  }

  return {
    health: () => request<Health>("/health"),
    restart: () => request("/restart", "POST"),
    updateStatus: () => request<UpdateStatus>("/update/status"),
    updateReleases: (page, size, prerelease, refresh = false) =>
      request<ReleaseListing>(
        "/update/releases?" +
          new URLSearchParams({
            page: String(page),
            size: String(size),
            prerelease: String(prerelease),
            refresh: String(refresh),
          }),
      ),
    updatePreview: (refresh = false) =>
      request<{ commit: CommitPreview | null }>(
        "/update/preview?refresh=" + String(refresh),
      ),
    inspectUpdate: (kind, ref) =>
      request<UpdateInspect>("/update/inspect", "POST", { kind, ref }),
    applyUpdate: (kind, ref) =>
      request<UpdateStatus>("/update/apply", "POST", { kind, ref }),
    config: () => request<Settings>("/config"),
    configSnapshot: async () => {
      const { data, revision } = await requestSnapshot<Settings>("/config");
      return { settings: data, revision };
    },
    tools: async (session = "web:owner") => {
      const { data, revision } = await requestSnapshot<ToolCatalog>(
        "/tools?session=" + encodeURIComponent(session),
      );
      return { ...data, revision };
    },
    saveConfig: (settings, revision) =>
      request<ConfigSaveResult>("/config", "PUT", settings, revision),
    startFeishuQr: (domain) =>
      request("/adapters/feishu/qr", "POST", { domain }),
    feishuQrStatus: (id) =>
      request("/adapters/feishu/qr/" + encodeURIComponent(id)),
    cancelFeishuQr: (id) =>
      request("/adapters/feishu/qr/" + encodeURIComponent(id), "DELETE"),
    applyFeishuQr: (id, adapter) =>
      request(
        "/adapters/feishu/qr/" + encodeURIComponent(id) + "/apply",
        "POST",
        adapter,
      ),
    diagnostics: () => request<DiagnosticsResult>("/diagnostics"),
    records: <K extends RecordKind>(kind: K) =>
      request<RecordMap[K][]>("/records/" + kind),
    recordsPage: <K extends RecordKind>(
      kind: K,
      page: number,
      pageSize: number,
      approvalFilter = "",
    ) =>
      request<Paginated<RecordMap[K]>>(
        "/records/" +
          kind +
          "?" +
          new URLSearchParams({
            page: String(page),
            page_size: String(pageSize),
            approval_filter: approvalFilter,
          }),
      ),
    stats: (hours, session) => {
      const params = new URLSearchParams({ hours: String(hours) });
      if (session !== undefined) params.set("session", session);
      return request<Statistics>("/stats?" + params);
    },
    statsSessions: () => request<string[]>("/stats/sessions"),
    exchangeRate: () => request<ExchangeRate>("/exchange-rate"),
    commands: (session) =>
      request<CommandInfo[]>("/commands?" + new URLSearchParams({ session })),
    helpMenuSections: () => request<HelpMenuSection[]>("/help-menu/sections"),
    helpMenuImage: (id) =>
      requestBlob("/help-images/" + encodeURIComponent(id)),
    webChats: () => request<WebChat[]>("/web-chats"),
    updateWebChat: (id, patch) =>
      request("/web-chats/" + encodeURIComponent(id), "PUT", patch),
    deleteWebChat: (id) =>
      request("/web-chats/" + encodeURIComponent(id), "DELETE"),
    history: (session) =>
      request<HistoryMessage[]>(
        "/history?timeline=true&session=" + encodeURIComponent(session),
      ),
    rawLogs: (lines, query) =>
      request<{ lines: string[] }>(
        "/logs/raw?lines=" + lines + "&q=" + encodeURIComponent(query),
      ),
    logs: ({ limit, level, source, query }) =>
      request<LogFeed>(
        "/logs?" +
          new URLSearchParams({
            limit: String(limit),
            level,
            source,
            q: query,
          }).toString(),
      ),
    logLevel: () => request<{ level: string }>("/logs/level"),
    setLogLevel: (level) =>
      request<{ level: string }>("/logs/level", "PUT", { level }),
    sendMessage: (text, session, clientId) =>
      request("/messages", "POST", { text, session, client_id: clientId }),
    resolveApproval: (id, allow) =>
      request("/approvals/" + encodeURIComponent(id), "POST", { allow }),
    memoryProfiles: () => request<MemoryProfile[]>("/memory/profiles"),
    memories: (kind = "", query = "", profile = "default") =>
      request<MemoryRecord[]>(
        "/memory?" +
          new URLSearchParams({ kind, q: query, profile }).toString(),
      ),
    memoriesPage: (kind, query, page, pageSize, profile = "default") =>
      request<Paginated<MemoryRecord>>(
        "/memory?" +
          new URLSearchParams({
            kind,
            profile,
            q: query,
            page: String(page),
            page_size: String(pageSize),
          }),
      ),
    memory: (id, profile = "default") =>
      request<MemoryDetail>(
        "/memory/" +
          encodeURIComponent(id) +
          "?" +
          new URLSearchParams({ profile }),
      ),
    memoryStatus: (profile = "default") =>
      request<MemoryStatus>(
        "/memory/status?" + new URLSearchParams({ profile }),
      ),
    memes: (category, query, page, pageSize) =>
      request<MemeListing>(
        "/memes?" +
          new URLSearchParams({
            category,
            q: query,
            page: String(page),
            page_size: String(pageSize),
          }),
      ),
    memeImage: (id) =>
      requestBlob("/memes/" + encodeURIComponent(id) + "/image"),
    uploadMeme: (file, category, description) =>
      request<MemeRecord>(
        "/memes?" + new URLSearchParams({ category, description }),
        "POST",
        file,
      ),
    updateMeme: (id, patch) =>
      request<MemeRecord>("/memes/" + encodeURIComponent(id), "PUT", patch),
    describeMeme: (id) =>
      request<MemeRecord>(
        "/memes/" + encodeURIComponent(id) + "/describe",
        "POST",
      ),
    deleteMeme: (id) => request("/memes/" + encodeURIComponent(id), "DELETE"),
    knowledgeLibraries: () =>
      request<KnowledgeLibrary[]>("/knowledge/libraries"),
    knowledge: (query, page, pageSize, library = "default") =>
      request<KnowledgeListing>(
        "/knowledge?" +
          new URLSearchParams({
            q: query,
            library,
            page: String(page),
            page_size: String(pageSize),
          }),
      ),
    knowledgeDocument: (id, start, count, library = "default") =>
      request<KnowledgeDetail>(
        "/knowledge/" +
          encodeURIComponent(id) +
          "?" +
          new URLSearchParams({
            start: String(start),
            count: String(count),
            library,
          }),
      ),
    knowledgeFile: async (id, library = "default") => {
      let response: Response;
      try {
        response = await fetch(
          "/api/knowledge/" +
            encodeURIComponent(id) +
            "/file?" +
            new URLSearchParams({ library }),
          { headers: { Authorization: "Bearer " + getToken() } },
        );
      } catch {
        throw new ApiError("无法连接后端服务，请确认进程仍在运行。", 0);
      }
      if (!response.ok)
        throw new ApiError(messageFor(response.status, null), response.status);
      return response.blob();
    },
    knowledgeSearch: (query, library = "default") =>
      request<KnowledgeHit[]>(
        "/knowledge/search?" + new URLSearchParams({ q: query, library }),
      ),
    uploadKnowledge: (file, library = "default") =>
      request<KnowledgeDocument>(
        "/knowledge?" + new URLSearchParams({ filename: file.name, library }),
        "POST",
        file,
      ),
    deleteKnowledge: (id, library = "default") =>
      request(
        "/knowledge/" +
          encodeURIComponent(id) +
          "?" +
          new URLSearchParams({ library }),
        "DELETE",
      ),
    routines: () => request<RoutineRecord[]>("/routines"),
    createRoutine: (draft) =>
      request<RoutineRecord>("/routines", "POST", draft),
    setRoutineStatus: (id, status) =>
      request<RoutineRecord>(
        "/routines/" + encodeURIComponent(id) + "/" + status,
        "POST",
      ),
    cancelSubagent: (id) =>
      request("/subagents/" + encodeURIComponent(id) + "/cancel", "POST"),
    detectSubagent: (id) =>
      request("/subagents/" + encodeURIComponent(id) + "/detect", "POST"),
    plugins: () => request<PluginInfo[]>("/plugins"),
    reloadPlugins: () => request<PluginInfo[]>("/plugins/reload", "POST"),
    reloadPlugin: (id) =>
      request<PluginInfo[]>(
        "/plugins/" + encodeURIComponent(id) + "/reload",
        "POST",
      ),
    installPluginZip: (id, file) =>
      request<PluginInfo[]>(
        "/plugins/install/zip/" + encodeURIComponent(id),
        "POST",
        file,
      ),
    installPluginGithub: (id, url) =>
      request<PluginInfo[]>("/plugins/install/github", "POST", { id, url }),
    removePlugin: (id) =>
      request<PluginInfo[]>("/plugins/" + encodeURIComponent(id), "DELETE"),
    skills: () => request<SkillInfo[]>("/skills"),
    skill: (name) =>
      request<SkillDetail>("/skills/" + encodeURIComponent(name)),
    installSkill: (file, replace) =>
      request<SkillInfo[]>(
        "/skills/install?replace=" + String(replace),
        "POST",
        file,
      ),
    removeSkill: (name) =>
      request<SkillInfo[]>("/skills/" + encodeURIComponent(name), "DELETE"),
    mcpServers: () => request<McpServerStatus[]>("/mcp"),
    reconnectMcp: (id) =>
      request<McpServerStatus[]>(
        "/mcp/" + encodeURIComponent(id) + "/reconnect",
        "POST",
      ),
    discoverModels: (provider) =>
      request<DiscoveredModel[]>("/providers/discover", "POST", provider),
    testModel: (provider, model) =>
      request<ModelTestResult>("/models/test", "POST", { provider, model }),
  };
}
