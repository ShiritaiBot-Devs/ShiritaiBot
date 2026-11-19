import { LoadingBar } from "../components/LoadingBar";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import type { MemoryKind, MemoryProfileConfig } from "../api/types";
import { adapterSessionId, sessionTitle } from "../lib/sessions";
import type { ConfigPageProps } from "./common";

const KINDS: { key: MemoryKind | ""; label: string }[] = [
  { key: "", label: "全部" },
  { key: "entity", label: "人物与事物" },
  { key: "fact", label: "事实" },
  { key: "event", label: "事件" },
  { key: "conflict", label: "待确认" },
];
const STATUS: Record<string, string> = {
  active: "有效",
  confirmed: "已确认",
  candidate: "新认识",
  conflicted: "有冲突",
  superseded: "已取代",
  archived: "已遗忘",
  open: "待主人确认",
  resolved: "已处理",
};

type MemoryPageProps = ConfigPageProps & {
  currentSession: string;
  draftSessions: string[];
};

const BUILTIN_PROFILES: Record<string, MemoryProfileConfig> = {
  default: { name: "默认记忆库", audience: "personal" },
  feishu: { name: "飞书记忆库", audience: "personal" },
};

function MemorySettings({
  client,
  config,
  setConfig,
  currentSession,
  draftSessions,
}: MemoryPageProps) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [audience, setAudience] =
    useState<MemoryProfileConfig["audience"]>("personal");
  const [session, setSession] = useState(
    currentSession.startsWith("background:") ? "web:owner" : currentSession,
  );
  const suggestions = useId();
  const observed = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
  });
  const memory = config.memory;
  const custom = memory.profiles ?? {};
  const bindings = memory.sessions ?? {};
  const profiles = { ...BUILTIN_PROFILES, ...custom };
  const ownerSessions = config.adapters.flatMap((adapter) =>
    adapter.owner_id
      ? [adapterSessionId(adapter, "private", adapter.owner_id)]
      : [],
  );
  const configured = config.adapters.flatMap((adapter) => [
    ...adapter.groups.map((target) =>
      adapterSessionId(adapter, "group", target),
    ),
    ...adapter.friends.map((target) =>
      adapterSessionId(adapter, "private", target),
    ),
  ]);
  const availableSessions = [
    ...new Set([
      "web:owner",
      ...draftSessions,
      ...ownerSessions,
      ...configured,
      ...Object.keys(bindings),
      ...(observed.data ?? []).map((row) => row.id),
    ]),
  ]
    .filter((value) => /^(web|private|group):/.test(value))
    .sort();
  const validSession =
    /^(?:web:[^:\s]{1,128}|(?:group|private):[A-Za-z0-9_-]{1,64}:[^:\s]{1,256})$/.test(
      session,
    );
  const personal =
    session.startsWith("web:") || ownerSessions.includes(session);
  const eligible = Object.entries(profiles).filter(
    ([, profile]) => profile.audience === (personal ? "personal" : "shared"),
  );

  function bind(value: string) {
    const sessions = { ...bindings };
    if (value) sessions[session] = value;
    else delete sessions[session];
    setConfig({ ...config, memory: { ...memory, sessions } });
  }

  return (
    <section className="panel">
      <h2>记忆库设置</h2>
      <p className="muted">
        个人库供主人会话使用。多个群聊或访客私聊可以显式共用同一个会话库。
      </p>
      <form
        className="inline"
        onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim() || Object.hasOwn(profiles, id)) return;
          setConfig({
            ...config,
            memory: {
              ...memory,
              profiles: { ...custom, [id]: { name: name.trim(), audience } },
            },
          });
          setId("");
          setName("");
        }}
      >
        <input
          aria-label="记忆库标识"
          placeholder="标识，例如 project"
          pattern="[a-z][a-z0-9_\-]{0,63}"
          value={id}
          onChange={(event) => setId(event.target.value)}
          required
        />
        <input
          aria-label="记忆库名称"
          placeholder="显示名称"
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <select
          aria-label="访问范围"
          value={audience}
          onChange={(event) =>
            setAudience(event.target.value === "shared" ? "shared" : "personal")
          }
        >
          <option value="personal">个人库</option>
          <option value="shared">会话库</option>
        </select>
        <button
          type="submit"
          disabled={!id || !name.trim() || Object.hasOwn(profiles, id)}
        >
          添加记忆库
        </button>
      </form>
      {Object.entries(custom).map(([key, profile]) => {
        const count = Object.values(bindings).filter(
          (value) => value === key,
        ).length;
        const usedByAdapter = config.adapters.some(
          (adapter) =>
            adapter.type === "feishu" && adapter.memory_profile === key,
        );
        return (
          <div className="inline" key={key}>
            <code>{key}</code>
            <input
              aria-label={`${key} 的显示名称`}
              value={profile.name}
              maxLength={80}
              onChange={(event) =>
                setConfig({
                  ...config,
                  memory: {
                    ...memory,
                    profiles: {
                      ...custom,
                      [key]: { ...profile, name: event.target.value },
                    },
                  },
                })
              }
            />
            <span className="muted">
              {profile.audience === "shared" ? "会话库" : "个人库"} · {count}{" "}
              个会话
            </span>
            <button
              type="button"
              disabled={count > 0 || usedByAdapter}
              title="移出配置会保留磁盘数据，重新添加同标识可恢复"
              onClick={() => {
                const next = { ...custom };
                delete next[key];
                setConfig({ ...config, memory: { ...memory, profiles: next } });
              }}
            >
              移出配置
            </button>
          </div>
        );
      })}
      <h3>会话绑定</h3>
      <div className="field-grid">
        <label className="field">
          <span className="field-name">会话</span>
          <input
            list={suggestions}
            placeholder="选择或输入完整会话 ID"
            value={session}
            onChange={(event) => setSession(event.target.value.trim())}
          />
          <datalist id={suggestions}>
            {availableSessions.map((value) => (
              <option key={value} value={value}>
                {sessionTitle(value)}
              </option>
            ))}
          </datalist>
        </label>
        <label className="field">
          <span className="field-name">使用的记忆库</span>
          <select
            disabled={!validSession}
            value={bindings[session] ?? ""}
            onChange={(event) => bind(event.target.value)}
          >
            <option value="">
              {personal ? "使用会话默认库" : "未绑定长期记忆库"}
            </option>
            {bindings[session] &&
              !eligible.some(([key]) => key === bindings[session]) && (
                <option value={bindings[session]} disabled>
                  已失效 · {bindings[session]}
                </option>
              )}
            {eligible.map(([key, profile]) => (
              <option key={key} value={key}>
                {profile.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {Object.entries(bindings).map(([key, value]) => (
        <div className="inline" key={key}>
          <button type="button" onClick={() => setSession(key)}>
            {sessionTitle(key)}
          </button>
          <span className="muted">{profiles[value]?.name ?? value}</span>
        </div>
      ))}
    </section>
  );
}

export function MemoryPage(props: MemoryPageProps) {
  const { client } = props;
  const [selectedProfile, setProfile] = useState("default");
  const profiles = useQuery({
    queryKey: ["memory-profiles"],
    queryFn: () => client.memoryProfiles(),
  });
  const profile = profiles.data?.some((row) => row.id === selectedProfile)
    ? selectedProfile
    : "default";
  const [kind, setKind] = useState<MemoryKind | "">("");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState("");
  const [page, setPage] = useState(1);
  const memories = useQuery({
    queryKey: ["memory", profile, kind, query, page],
    queryFn: () => client.memoriesPage(kind, query, page, PAGE_SIZE, profile),
    refetchInterval: 10000,
  });
  const detail = useQuery({
    queryKey: ["memory-detail", profile, open],
    queryFn: () => client.memory(open, profile),
    enabled: Boolean(open),
  });

  return (
    <>
      <MemorySettings {...props} />
      <section className="panel">
        <div className="panel-head">
          <h2>浏览记忆</h2>
          <select
            aria-label="浏览的记忆库"
            value={profile}
            onChange={(event) => {
              setProfile(event.target.value);
              setPage(1);
              setOpen("");
            }}
          >
            {(profiles.data ?? [{ id: "default", name: "默认记忆库" }]).map(
              (row) => (
                <option key={row.id} value={row.id}>
                  {row.name}
                </option>
              ),
            )}
          </select>
          <div className="range-tabs" role="group" aria-label="记忆类型">
            {KINDS.map(({ key, label }) => (
              <button
                type="button"
                key={key || "all"}
                className={kind === key ? "selected" : ""}
                aria-pressed={kind === key}
                onClick={() => {
                  setKind(key);
                  setPage(1);
                  setOpen("");
                }}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <p className="muted">
          {profiles.data?.find((row) => row.id === profile)?.audience ===
          "shared"
            ? "会话库从绑定会话中提取记忆。"
            : "可以在主人对话里让 Agent 记住、忘掉或更正一条记忆。"}
        </p>
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
            setOpen("");
          }}
        >
          <input
            aria-label="搜索记忆"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="人名、事物或关键词（包括已取代和已遗忘的条目）"
          />
          <button type="submit">
            <Search size={16} />
            搜索
          </button>
        </form>
        {memories.error && (
          <QueryErrorNotice
            message={`读取记忆失败：${memories.error.message}`}
          />
        )}
        {memories.data?.items.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <span className="tag">
                {row.id} · {STATUS[row.status] ?? row.status}
              </span>
              <button
                type="button"
                aria-expanded={open === row.id}
                onClick={() => setOpen(open === row.id ? "" : row.id)}
              >
                {open === row.id ? "收起" : "详情与证据"}
              </button>
            </div>
            <p
              className={
                row.status === "archived" || row.status === "superseded"
                  ? "muted"
                  : ""
              }
            >
              {row.text}
            </p>
            {open === row.id && (
              <div className="memory-detail">
                {detail.isFetching && <LoadingBar label="读取记忆详情" />}
                {detail.error && (
                  <QueryErrorNotice
                    message={`读取详情失败：${detail.error.message}`}
                  />
                )}
                {detail.data?.id === row.id && (
                  <>
                    {[
                      ...(detail.data.facts ?? []),
                      ...(detail.data.events ?? []),
                      ...(detail.data.items ?? []),
                    ].map((line) => (
                      <p key={line}>{line}</p>
                    ))}
                    {detail.data.history?.length ? (
                      <>
                        <h4>历史</h4>
                        {detail.data.history.map((line) => (
                          <p className="muted" key={line}>
                            {line}
                          </p>
                        ))}
                      </>
                    ) : null}
                    {detail.data.evidence?.map((item, position) => (
                      <blockquote key={position}>
                        “{item.quote}”
                        <small className="muted">
                          {" "}
                          —— {item.source}，{item.recorded}
                        </small>
                      </blockquote>
                    ))}
                    {detail.data.resolution && (
                      <p className="muted">
                        处理结果：{detail.data.resolution}
                      </p>
                    )}
                  </>
                )}
              </div>
            )}
          </article>
        ))}
        {!memories.data?.items.length &&
          !memories.error &&
          !memories.isPending && (
            <EmptyState
              text={query ? "没有匹配的记忆。" : "这个库还没有记忆。"}
            />
          )}
        {memories.isPending && <LoadingBar label="正在读取记忆…" />}
        <Pagination
          label="记忆库"
          page={memories.data?.page ?? page}
          total={memories.data?.total ?? 0}
          loading={memories.isFetching}
          onChange={(next) => {
            setPage(next);
            setOpen("");
          }}
        />
      </section>
    </>
  );
}
