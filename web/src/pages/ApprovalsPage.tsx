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
import i18n from "../i18n";

const MAIN_SESSION = /^(web|private|group):[^\s]{1,200}$/;
type Target =
  | "session"
  | "owner_default"
  | "private_default"
  | "group_default"
  | "proactive_default";

function effectivePermission(tool?: ToolInfo) {
  if (!tool) return i18n.t("approvals:unavailable");
  if (tool.decision === "approve") return PERMISSIONS.manual;
  if (tool.decision === "deny" && tool.permission !== "deny")
    return i18n.t("approvals:unavailable");
  return (
    PERMISSIONS[tool.decision as ToolPermission] ?? i18n.t("approvals:unknown")
  );
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
        allow ? i18n.t("approvals:allowed") : i18n.t("approvals:denied"),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>{i18n.t("approvals:availability.title")}</h2>
        <p className="muted">{i18n.t("approvals:availability.hint")}</p>
        <div className="field-grid">
          <label className="field">
            {i18n.t("approvals:scope")}
            <select
              value={target}
              onChange={(event) => {
                setTarget(event.target.value as Target);
                setToolPage(1);
              }}
            >
              <option value="session">
                {i18n.t("approvals:scopeSession")}
              </option>
              <option value="owner_default">
                {i18n.t("approvals:scopeOwner")}
              </option>
              <option value="private_default">
                {i18n.t("approvals:scopePrivate")}
              </option>
              <option value="group_default">
                {i18n.t("approvals:scopeGroup")}
              </option>
              <option value="proactive_default">
                {i18n.t("approvals:scopeProactive")}
              </option>
            </select>
          </label>
          {target === "session" && (
            <div className="field">
              <label htmlFor="approval-session">
                {i18n.t("approvals:session")}
              </label>
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
                    {i18n.t("approvals:resetDefault")}
                  </button>
                )}
              </div>
            </div>
          )}
          {target !== "proactive_default" && (
            <label className="field">
              {i18n.t("approvals:mode.label")}
              <select
                aria-label={i18n.t("approvals:mode.label")}
                value={mode}
                disabled={forcedChat}
                onChange={(event) =>
                  update({
                    ...profile,
                    mode: event.target.value as SessionPolicy["mode"],
                  })
                }
              >
                <option value="chat">{i18n.t("approvals:mode.chat")}</option>
                <option value="agent">{i18n.t("approvals:mode.agent")}</option>
              </select>
              {forcedChat && (
                <small>{i18n.t("approvals:mode.feishuGroup")}</small>
              )}
            </label>
          )}
          {target !== "proactive_default" && (
            <label className="field">
              {i18n.t("approvals:sandbox.label")}
              <select
                value={
                  (profile as SessionPolicy).shell_workspace_isolation
                    ? "on"
                    : "off"
                }
                onChange={(event) =>
                  update({
                    ...(profile as SessionPolicy),
                    shell_workspace_isolation: event.target.value === "on",
                  })
                }
              >
                <option value="off">{i18n.t("approvals:off")}</option>
                <option value="on">{i18n.t("approvals:on")}</option>
              </select>
              <small>{i18n.t("approvals:sandbox.help")}</small>
            </label>
          )}
          <label className="field">
            {i18n.t("approvals:preset.label")}
            <select
              aria-label={i18n.t("approvals:preset.label")}
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
                  {i18n.t("approvals:preset.custom")}
                </option>
              )}
              {PERMISSION_PRESETS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
            <small>{i18n.t("approvals:preset.help")}</small>
          </label>
        </div>
        <div
          className="risk-matrix"
          aria-label={i18n.t("approvals:riskMatrix")}
        >
          {RISK_ORDER.map((level) => (
            <div className="risk-matrix-row" key={level}>
              <div>
                <strong>{RISK_META[level].label}</strong>
                <span className="badge">{level}</span>
                <p className="muted">{RISK_META[level].description}</p>
              </div>
              <label className="field">
                {i18n.t("approvals:riskDefault", {
                  risk: RISK_META[level].label,
                })}
                <select
                  aria-label={i18n.t("approvals:riskDefault", {
                    risk: RISK_META[level].label,
                  })}
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
            {i18n.t("approvals:unsaved")}
          </p>
        )}
        {target === "proactive_default" && (
          <p className="muted">{i18n.t("approvals:proactiveHint")}</p>
        )}
        {mode === "chat" && !forcedChat && target !== "session" && (
          <p className="muted">{i18n.t("approvals:chatHint")}</p>
        )}
        {catalog.error && (
          <QueryErrorNotice
            message={i18n.t("approvals:toolsFailed", {
              error: catalog.error.message,
            })}
          />
        )}
        {sessions.error && (
          <QueryErrorNotice
            message={i18n.t("approvals:sessionsFailed", {
              error: sessions.error.message,
            })}
          />
        )}
        <div className="field-grid">
          <label className="field">
            {i18n.t("approvals:search.label")}
            <input
              type="search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                setToolPage(1);
              }}
              placeholder={i18n.t("approvals:search.placeholder")}
            />
          </label>
          <label className="field">
            {i18n.t("approvals:riskFilter")}
            <select
              aria-label={i18n.t("approvals:riskFilter")}
              value={riskFilter}
              onChange={(event) => {
                setRiskFilter(event.target.value as ToolRisk | "all");
                setToolPage(1);
              }}
            >
              <option value="all">{i18n.t("approvals:allRisks")}</option>
              {RISK_ORDER.map((level) => (
                <option key={level} value={level}>
                  {i18n.t("withDetail", {
                    label: RISK_META[level].label,
                    detail: level,
                  })}
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
            <span>
              {i18n.t("approvals:onlyCustom", { count: customToolCount })}
            </span>
          </label>
        </div>
        <div className="tool-permission-list">
          {visibleTools.length > 0 && (
            <div className="table-scroll">
              <table
                className="tool-table"
                aria-label={i18n.t("approvals:toolTable")}
              >
                <colgroup>
                  <col className="tool-name-col" />
                  <col className="tool-risk-col" />
                  <col />
                  <col className="tool-permission-col" />
                  <col className="tool-status-col" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">{i18n.t("approvals:col.name")}</th>
                    <th scope="col">{i18n.t("approvals:col.risk")}</th>
                    <th scope="col">{i18n.t("approvals:col.details")}</th>
                    <th scope="col">{i18n.t("approvals:col.permission")}</th>
                    <th scope="col">{i18n.t("approvals:col.status")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleTools.map((name) => {
                    const tool = tools.find((tool) => tool.name === name);
                    const level = toolRisk(tool);
                    const fallbackPerm = inheritedPermission(profile, tool);
                    const permission = profile.tools[name] ?? fallbackPerm;
                    const description = tool
                      ? tool.description?.trim() ||
                        i18n.t("approvals:noDescription")
                      : i18n.t("approvals:pluginNotLoaded");
                    return (
                      <tr key={name}>
                        <th scope="row">{name}</th>
                        <td>
                          <span
                            className="tool-risk-pill"
                            data-risk={tool ? level : "unknown"}
                          >
                            {tool
                              ? RISK_META[level].label
                              : i18n.t("approvals:unknown")}
                          </span>
                        </td>
                        <td>
                          <details className="tool-details">
                            <summary>
                              <span className="tool-description-preview">
                                {description}
                              </span>
                              <span className="tool-description-source">
                                {i18n.t("approvals:sourcePlugin", {
                                  plugin:
                                    tool?.plugin ??
                                    i18n.t("approvals:notLoaded"),
                                })}
                              </span>
                              <ChevronDown size={14} aria-hidden="true" />
                            </summary>
                            <p>{description}</p>
                          </details>
                        </td>
                        <td>
                          <select
                            aria-label={i18n.t("approvals:toolPermission", {
                              name,
                            })}
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
                              {i18n.t("approvals:followPreset", {
                                permission: PERMISSIONS[fallbackPerm],
                              })}
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
                              ? i18n.t("approvals:unavailableInMode")
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
            <LoadingBar label={i18n.t("approvals:loadingTools")} />
          )}
          {!catalog.isPending &&
            !catalog.isError &&
            !refreshingConfig &&
            !toolNames.length && (
              <EmptyState text={i18n.t("approvals:noTools")} />
            )}
          {!catalog.isPending &&
            toolNames.length > 0 &&
            !filteredTools.length && (
              <EmptyState text={i18n.t("approvals:noMatch")} />
            )}
        </div>
        <Pagination
          label={i18n.t("approvals:toolList")}
          page={currentToolPage}
          total={filteredTools.length}
          onChange={setToolPage}
        />
      </section>
      <section className="panel">
        <h2>{i18n.t("approvals:approvalSettings")}</h2>
        <p className="muted">{i18n.t("approvals:approvalSettingsHint")}</p>
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
        <h2>
          {i18n.t("approvals:pendingTitle", {
            count: approvals.data?.total ?? 0,
          })}
        </h2>
        {approvals.error && (
          <QueryErrorNotice
            message={i18n.t("approvals:approvalsFailed", {
              error: approvals.error.message,
            })}
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
            !approvals.error && (
              <EmptyState text={i18n.t("approvals:noPending")} />
            )}
        <Pagination
          label={i18n.t("approvals:pendingList")}
          page={approvals.data?.page ?? pendingPage}
          total={approvals.data?.total ?? 0}
          loading={approvals.isFetching || busy}
          onChange={setPendingPage}
        />
      </section>
      <section className="panel">
        <h2>{i18n.t("approvals:history.title")}</h2>
        {history.error && (
          <QueryErrorNotice
            message={i18n.t("approvals:historyFailed", {
              error: history.error.message,
            })}
          />
        )}
        {reviews.length
          ? reviews.map((row) => (
              <article className="tool-permission-row" key={row.id}>
                <div>
                  <strong>{row.tool}</strong>
                  <span className="badge">
                    {row.reviewer === "model"
                      ? i18n.t("approvals:reviewer.model")
                      : i18n.t("approvals:reviewer.manual")}
                  </span>
                  <span className="badge">
                    {row.status === "approved"
                      ? i18n.t("approvals:status.approved")
                      : row.status === "denied"
                        ? i18n.t("approvals:status.denied")
                        : row.status}
                  </span>
                  <p className="muted">
                    {row.scope} ·{" "}
                    {row.reason ?? i18n.t("approvals:status.reviewing")}
                  </p>
                  <details>
                    <summary>{i18n.t("approvals:arguments")}</summary>
                    <pre>{JSON.stringify(row.args, null, 2)}</pre>
                  </details>
                </div>
              </article>
            ))
          : !history.isPending &&
            !history.error && (
              <EmptyState text={i18n.t("approvals:noHistory")} />
            )}
        <Pagination
          label={i18n.t("approvals:historyList")}
          page={history.data?.page ?? reviewPage}
          total={history.data?.total ?? 0}
          loading={history.isFetching}
          onChange={setReviewPage}
        />
      </section>
    </>
  );
}
