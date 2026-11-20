import { ChevronDown } from "lucide-react";
import { LoadingBar } from "../components/LoadingBar";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApprovalCard } from "../components/ApprovalCard";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { POLICY_FIELDS } from "../config/schema";
import { CONFIG_SNAPSHOT_KEY } from "../hooks/useConfigDraft";
import { SessionOptions } from "../components/SessionPicker";
import { adapterSessionId, sessionRole } from "../lib/sessions";
import {
  PERMISSIONS,
  PERMISSION_PRESETS,
  RISK_META,
  RISK_ORDER,
  inheritedPermission,
  matchPreset,
  normalizeRisks,
  presetRisks,
  toolRisk,
} from "../lib/permissions";
import type {
  ConfigSnapshot,
  PermissionPreset,
  SessionPolicy,
  ToolInfo,
  ToolPermission,
  ToolPolicy,
  ToolRisk,
} from "../api/types";
import type { ConfigPageProps } from "./common";

const MAIN_SESSION = /^(web|private|group):[^\s]{1,200}$/;
type Target =
  | "session"
  | "owner_default"
  | "private_default"
  | "group_default"
  | "proactive_default";

function effectivePermission(tool?: ToolInfo) {
  if (!tool) return "不可用";
  if (tool.decision === "approve") return PERMISSIONS.manual;
  if (tool.decision === "deny" && tool.permission !== "deny") return "不可用";
  return PERMISSIONS[tool.decision as ToolPermission] ?? "未知";
}

export function ApprovalsPage({
  client,
  config,
  setConfig,
  act,
  saved,
  initialSession = "web:owner",
}: ConfigPageProps & { saved?: ConfigSnapshot; initialSession?: string }) {
  const [target, setTarget] = useState<Target>("session");
  const [session, setSession] = useState(
    MAIN_SESSION.test(initialSession) ? initialSession : "web:owner",
  );
  const [search, setSearch] = useState("");
  const [riskFilter, setRiskFilter] = useState<ToolRisk | "all">("all");
  const [onlyCustom, setOnlyCustom] = useState(false);
  const [busy, setBusy] = useState(false);
  const [toolPage, setToolPage] = useState(1);
  const [pendingPage, setPendingPage] = useState(1);
  const [reviewPage, setReviewPage] = useState(1);
  const queryClient = useQueryClient();
  const catalogSession = target === "session" ? session : "web:owner";
  const revision = saved?.revision;
  const catalog = useQuery({
    queryKey: ["tools", catalogSession, revision],
    queryFn: () => client.tools(catalogSession),
    refetchInterval: 10000,
  });
  const refreshingConfig = Boolean(
    revision && catalog.data?.revision && catalog.data.revision !== revision,
  );
  useEffect(() => {
    if (refreshingConfig)
      void queryClient.invalidateQueries({ queryKey: CONFIG_SNAPSHOT_KEY });
  }, [catalog.data?.revision, queryClient, refreshingConfig]);
  const effective =
    !catalog.isError && !refreshingConfig && catalog.data?.session === session
      ? catalog.data
      : undefined;
  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
  });
  const approvals = useQuery({
    queryKey: ["records", "approval", "pending", pendingPage],
    queryFn: () =>
      client.recordsPage("approval", pendingPage, PAGE_SIZE, "pending"),
    refetchInterval: 4000,
  });
  const history = useQuery({
    queryKey: ["records", "approval", "history", reviewPage],
    queryFn: () =>
      client.recordsPage("approval", reviewPage, PAGE_SIZE, "history"),
    refetchInterval: 4000,
  });
  const pending = approvals.data?.items ?? [];
  const reviews = history.data?.items ?? [];
  const policy = config.policy;
  const savedConfig = saved?.settings ?? config;
  const dirtyPolicy =
    JSON.stringify(policy) !== JSON.stringify(savedConfig.policy);
  const row = sessions.data?.find((row) => row.id === session);
  const role = sessionRole(session);
  const owner =
    role === "feishu-private" && effective
      ? effective.active !== false
      : session.startsWith("web:") ||
        row?.owner ||
        savedConfig.adapters.some(
          (a) =>
            a.owner_id &&
            session === adapterSessionId(a, "private", a.owner_id),
        );
  const fallback = session.startsWith("group:")
    ? policy.group_default
    : owner
      ? policy.owner_default
      : policy.private_default;
  const profile: ToolPolicy | SessionPolicy =
    target === "session"
      ? (policy.sessions[session] ?? fallback)
      : policy[target];
  const forcedChat = target === "session" && role === "feishu-group";
  const mode = forcedChat
    ? "chat"
    : target === "proactive_default"
      ? "agent"
      : (profile as SessionPolicy).mode;
  const configuredFeishuSessions = savedConfig.adapters
    .filter((a) => a.type === "feishu" && a.enabled && a.app_id)
    .flatMap((a) => [
      ...(a.owner_id ? [adapterSessionId(a, "private", a.owner_id)] : []),
      ...a.groups.map((group) => adapterSessionId(a, "group", group)),
    ]);
  const knownSessions = [
    "web:owner",
    session,
    ...Object.keys(savedConfig.policy.sessions),
    ...Object.keys(policy.sessions),
    ...configuredFeishuSessions,
    ...(sessions.data ?? [])
      .filter((row) => !row.background)
      .map((row) => row.id),
  ].filter((id) => MAIN_SESSION.test(id));
  const tools = (refreshingConfig ? undefined : catalog.data?.tools) ?? [];
  const toolNames = [
    ...new Set([
      ...tools.map((tool) => tool.name),
      ...Object.keys(profile.tools),
    ]),
  ].sort();

  const profileRisks = normalizeRisks(profile.risks);
  const matchedPreset = matchPreset(profileRisks);
  const customToolCount = Object.keys(profile.tools).length;

  const filteredTools = toolNames.filter((name) => {
    const tool = tools.find((tool) => tool.name === name);
    if (onlyCustom && !(name in profile.tools)) return false;
    if (riskFilter !== "all" && toolRisk(tool) !== riskFilter) return false;
    return `${name} ${tool?.plugin ?? ""} ${tool?.description ?? ""}`
      .toLowerCase()
      .includes(search.toLowerCase());
  });
  const currentToolPage = Math.min(
    toolPage,
    Math.max(1, Math.ceil(filteredTools.length / PAGE_SIZE)),
  );
  const visibleTools = filteredTools.slice(
    (currentToolPage - 1) * PAGE_SIZE,
    currentToolPage * PAGE_SIZE,
  );

  function update(next: ToolPolicy | SessionPolicy) {
    setConfig({
      ...config,
      policy:
        target === "session"
          ? {
              ...policy,
              sessions: {
                ...policy.sessions,
                [session]: forcedChat
                  ? { ...next, mode: "chat" }
                  : (next as SessionPolicy),
              },
            }
          : { ...policy, [target]: next },
    });
  }

  function restoreSessionDefaults() {
    const sessions = { ...policy.sessions };
    delete sessions[session];
    setConfig({ ...config, policy: { ...policy, sessions } });
  }

  async function resolve(id: string, allow: boolean) {
    setBusy(true);
    try {
      await act(
        () => client.resolveApproval(id, allow),
        allow ? "已允许" : "已拒绝",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>工具可用性</h2>
        <p className="muted">选择会话，查看当前可用的工具和审批方式。</p>
        <div className="field-grid">
          <label className="field">
            配置范围
            <select
              value={target}
              onChange={(event) => {
                setTarget(event.target.value as Target);
                setToolPage(1);
              }}
            >
              <option value="session">指定 Main 会话</option>
              <option value="owner_default">主人私聊 / WebUI 默认</option>
              <option value="private_default">其他私聊默认</option>
              <option value="group_default">群聊默认</option>
              <option value="proactive_default">新 Proactive Loop 默认</option>
            </select>
          </label>
          {target === "session" && (
            <div className="field">
              <label htmlFor="approval-session">会话</label>
              <div className="inline">
                <select
                  id="approval-session"
                  value={session}
                  onChange={(event) => {
                    setSession(event.target.value);
                    setToolPage(1);
                  }}
                >
                  <SessionOptions ids={knownSessions} />
                </select>
                {policy.sessions[session] && (
                  <button type="button" onClick={restoreSessionDefaults}>
                    恢复默认配置
                  </button>
                )}
              </div>
            </div>
          )}
          {target !== "proactive_default" && (
            <label className="field">
              会话模式
              <select
                aria-label="会话模式"
                value={mode}
                disabled={forcedChat}
                onChange={(event) =>
                  update({
                    ...profile,
                    mode: event.target.value as SessionPolicy["mode"],
                  })
                }
              >
                <option value="chat">Chat · 基础对话</option>
                <option value="agent">Agent · 允许工具调用</option>
              </select>
              {forcedChat && <small>飞书群聊固定使用 Chat。</small>}
            </label>
          )}
          <label className="field">
            权限预设
            <select
              aria-label="权限预设"
              value={matchedPreset}
              onChange={(event) => {
                const value = event.target.value;
                if (value === "custom") return;
                update({
                  ...profile,
                  risks: presetRisks(value as PermissionPreset),
                  tools: {},
                });
              }}
            >
              {matchedPreset === "custom" && (
                <option value="custom" disabled>
                  自定义（已调整下方风险等级）
                </option>
              )}
              {PERMISSION_PRESETS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <small>切换预设会填充下方风险矩阵并清除逐工具覆盖。</small>
          </label>
        </div>
        <div className="risk-matrix" aria-label="按风险等级配置默认权限">
          {RISK_ORDER.map((level) => (
            <div className="risk-matrix-row" key={level}>
              <div>
                <strong>{RISK_META[level].label}</strong>
                <span className="badge">{level}</span>
                <p className="muted">{RISK_META[level].description}</p>
              </div>
              <label className="field">
                {RISK_META[level].label}工具默认权限
                <select
                  aria-label={`${RISK_META[level].label}工具默认权限`}
                  value={profileRisks[level]}
                  onChange={(event) =>
                    update({
                      ...profile,
                      risks: {
                        ...profileRisks,
                        [level]: event.target.value as ToolPermission,
                      },
                    })
                  }
                >
                  {Object.entries(PERMISSIONS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          ))}
        </div>
        {dirtyPolicy && (
          <p className="muted" role="status">
            权限修改待保存
          </p>
        )}
        {target === "proactive_default" && (
          <p className="muted">
            仅新建的 Proactive Loop
            使用此默认值，运行中的任务保留创建时的权限快照。人工审批会发送给主人，并等待审批或超时。
          </p>
        )}
        {mode === "chat" && !forcedChat && target !== "session" && (
          <p className="muted">
            Chat 模式只开放基础对话工具，例如时间、网页搜索和群聊上下文查询。
          </p>
        )}
        {catalog.error && (
          <QueryErrorNotice
            message={`读取工具失败：${catalog.error.message}`}
          />
        )}
        {sessions.error && (
          <QueryErrorNotice
            message={`读取会话失败：${sessions.error.message}`}
          />
        )}
        <div className="field-grid">
          <label className="field">
            搜索工具
            <input
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setToolPage(1);
              }}
              placeholder="工具名、插件或说明"
            />
          </label>
          <label className="field">
            按风险等级筛选
            <select
              aria-label="按风险等级筛选"
              value={riskFilter}
              onChange={(event) => {
                setRiskFilter(event.target.value as ToolRisk | "all");
                setToolPage(1);
              }}
            >
              <option value="all">全部风险等级</option>
              {RISK_ORDER.map((level) => (
                <option key={level} value={level}>
                  {RISK_META[level].label}（{level}）
                </option>
              ))}
            </select>
          </label>
          <label className="field risk-filter-toggle">
            <input
              type="checkbox"
              checked={onlyCustom}
              onChange={(event) => {
                setOnlyCustom(event.target.checked);
                setToolPage(1);
              }}
            />
            <span>仅看已自定义覆盖（{customToolCount}）</span>
          </label>
        </div>
        <div className="tool-permission-list">
          {visibleTools.length > 0 && (
            <div className="table-scroll">
              <table className="tool-table" aria-label="工具权限">
                <colgroup>
                  <col className="tool-name-col" />
                  <col className="tool-risk-col" />
                  <col />
                  <col className="tool-permission-col" />
                  <col className="tool-status-col" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">工具名</th>
                    <th scope="col">风险等级</th>
                    <th scope="col">详细信息</th>
                    <th scope="col">权限</th>
                    <th scope="col">当前状态</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleTools.map((name) => {
                    const tool = tools.find((tool) => tool.name === name);
                    const level = toolRisk(tool);
                    const fallbackPerm = inheritedPermission(profile, tool);
                    const permission = profile.tools[name] ?? fallbackPerm;
                    const description = tool
                      ? tool.description?.trim() || "暂无工具说明。"
                      : "插件未加载，保留的权限配置将在工具重新注册后使用。";
                    return (
                      <tr key={name}>
                        <th scope="row">{name}</th>
                        <td>
                          <span
                            className="tool-risk-pill"
                            data-risk={tool ? level : "unknown"}
                          >
                            {tool ? RISK_META[level].label : "未知"}
                          </span>
                        </td>
                        <td>
                          <details className="tool-details">
                            <summary>
                              <span className="tool-description-preview">
                                {description}
                              </span>
                              <span className="tool-description-source">
                                来源插件 · {tool?.plugin ?? "未加载"}
                              </span>
                              <ChevronDown size={14} aria-hidden="true" />
                            </summary>
                            <p>{description}</p>
                          </details>
                        </td>
                        <td>
                          <select
                            aria-label={`${name} 权限`}
                            value={profile.tools[name] ?? "inherit"}
                            onChange={(event) => {
                              const next = { ...profile.tools };
                              if (event.target.value === "inherit")
                                delete next[name];
                              else
                                next[name] = event.target
                                  .value as ToolPermission;
                              update({ ...profile, tools: next });
                            }}
                          >
                            <option value="inherit">
                              跟随预设 · {PERMISSIONS[fallbackPerm]}
                            </option>
                            {Object.entries(PERMISSIONS).map(
                              ([value, label]) => (
                                <option key={value} value={value}>
                                  {label}
                                </option>
                              ),
                            )}
                          </select>
                        </td>
                        <td>
                          {target === "session"
                            ? effective
                              ? effectivePermission(tool)
                              : "—"
                            : !tool || (mode === "chat" && !tool.chat)
                              ? "此模式下不可用"
                              : PERMISSIONS[permission]}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {(catalog.isPending || refreshingConfig) && (
            <LoadingBar label="读取工具权限" />
          )}
          {!catalog.isPending &&
            !catalog.isError &&
            !refreshingConfig &&
            !toolNames.length && <EmptyState text="尚无已注册工具。" />}
          {!catalog.isPending &&
            toolNames.length > 0 &&
            !filteredTools.length && <EmptyState text="没有匹配的工具。" />}
        </div>
        <Pagination
          label="工具列表"
          page={currentToolPage}
          total={filteredTools.length}
          onChange={setToolPage}
        />
      </section>
      <section className="panel">
        <h2>审批与执行设置</h2>
        <p className="muted">
          自动审批模型在“模型与服务 →
          工具自动审批模型”路由中设置，可配置备用模型和 Jev 判断模型；Jev
          拒绝或把握不足时可交给路由里的文本模型复核。最终拒绝、
          超时或返回无效时均不执行。
        </p>
        <div className="field-grid">
          {POLICY_FIELDS.map((spec) => (
            <Field
              key={spec.key}
              spec={spec}
              value={policy[spec.key as keyof typeof policy]}
              onChange={(next) =>
                setConfig({
                  ...config,
                  policy: { ...policy, [spec.key]: next },
                })
              }
            />
          ))}
        </div>
      </section>
      <section className="panel">
        <h2>待人工审批 · {approvals.data?.total ?? 0}</h2>
        {approvals.error && (
          <QueryErrorNotice
            message={`读取审批失败：${approvals.error.message}`}
          />
        )}
        {pending.length
          ? pending.map((row) => (
              <ApprovalCard
                key={row.id}
                approval={row}
                busy={busy}
                onResolve={(allow) => void resolve(row.id, allow)}
              />
            ))
          : !approvals.isPending &&
            !approvals.error && <EmptyState text="当前没有待人工审批操作。" />}
        <Pagination
          label="待人工审批"
          page={approvals.data?.page ?? pendingPage}
          total={approvals.data?.total ?? 0}
          loading={approvals.isFetching || busy}
          onChange={setPendingPage}
        />
      </section>
      <section className="panel">
        <h2>审批记录</h2>
        {history.error && (
          <QueryErrorNotice
            message={`读取审批记录失败：${history.error.message}`}
          />
        )}
        {reviews.length
          ? reviews.map((row) => (
              <article className="tool-permission-row" key={row.id}>
                <div>
                  <strong>{row.tool}</strong>
                  <span className="badge">
                    {row.reviewer === "model" ? "模型自动审批" : "人工审批"}
                  </span>
                  <span className="badge">
                    {row.status === "approved"
                      ? "已批准"
                      : row.status === "denied"
                        ? "已拒绝"
                        : row.status}
                  </span>
                  <p className="muted">
                    {row.scope} · {row.reason ?? "正在审查"}
                  </p>
                  <details>
                    <summary>调用参数</summary>
                    <pre>{JSON.stringify(row.args, null, 2)}</pre>
                  </details>
                </div>
              </article>
            ))
          : !history.isPending &&
            !history.error && <EmptyState text="暂无审批记录。" />}
        <Pagination
          label="审批记录"
          page={history.data?.page ?? reviewPage}
          total={history.data?.total ?? 0}
          loading={history.isFetching}
          onChange={setReviewPage}
        />
      </section>
    </>
  );
}
