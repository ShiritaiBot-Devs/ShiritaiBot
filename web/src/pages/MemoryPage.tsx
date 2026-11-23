import { LoadingBar } from "../components/LoadingBar";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Search } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import { SessionPicker } from "../components/SessionPicker";
import type { MemoryKind, MemoryProfileConfig } from "../api/types";
import { adapterSessionId, isMainSession, sessionTitle } from "../lib/sessions";
import type { SessionConfigPageProps } from "./common";
import i18n, { tx } from "../i18n";

const KINDS: { key: MemoryKind | ""; label: string }[] = [
  { key: "", label: "memory:kinds.all" },
  { key: "entity", label: "memory:kinds.entity" },
  { key: "fact", label: "memory:kinds.fact" },
  { key: "event", label: "memory:kinds.event" },
  { key: "conflict", label: "memory:kinds.conflict" },
];
const STATUS: Record<string, string> = {
  active: "memory:status.active",
  confirmed: "memory:status.confirmed",
  candidate: "memory:status.candidate",
  conflicted: "memory:status.conflicted",
  superseded: "memory:status.superseded",
  archived: "memory:status.archived",
  open: "memory:status.open",
  resolved: "memory:status.resolved",
};

const BUILTIN_PROFILES: Record<string, MemoryProfileConfig> = {
  default: {
    get name() {
      return i18n.t("memory:profiles.default");
    },
    audience: "personal",
  },
  feishu: {
    get name() {
      return i18n.t("memory:profiles.feishu");
    },
    audience: "personal",
  },
};

function MemorySettings({
  client,
  config,
  setConfig,
  currentSession,
  draftSessions,
}: SessionConfigPageProps) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [audience, setAudience] =
    useState<MemoryProfileConfig["audience"]>("personal");
  const [session, setSession] = useState(
    isMainSession(currentSession) ? currentSession : "web:owner",
  );
  const memory = config.memory;
  const custom = memory.profiles ?? {};
  const bindings = memory.sessions ?? {};
  const profiles = { ...BUILTIN_PROFILES, ...custom };
  const ownerSessions = config.adapters.flatMap((adapter) =>
    adapter.owner_id
      ? [adapterSessionId(adapter, "private", adapter.owner_id)]
      : [],
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
    <section className="panel memory-settings">
      <h2>{i18n.t("memory:settings.title")}</h2>
      <p className="muted">{i18n.t("memory:settings.hint")}</p>
      <div className="memory-settings-columns">
        <section className="memory-settings-section">
          <h3>{i18n.t("memory:custom.title")}</h3>
          <form
            className="memory-settings-fields"
            onSubmit={(event) => {
              event.preventDefault();
              if (!name.trim() || Object.hasOwn(profiles, id)) return;
              setConfig({
                ...config,
                memory: {
                  ...memory,
                  profiles: {
                    ...custom,
                    [id]: { name: name.trim(), audience },
                  },
                },
              });
              setId("");
              setName("");
            }}
          >
            <label className="field">
              <span>{i18n.t("memory:custom.id")}</span>
              <input
                aria-label={i18n.t("memory:custom.idLabel")}
                placeholder={i18n.t("memory:custom.idPlaceholder")}
                pattern="[a-z][a-z0-9_\-]{0,63}"
                value={id}
                onChange={(event) => setId(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>{i18n.t("memory:custom.name")}</span>
              <input
                aria-label={i18n.t("memory:custom.nameLabel")}
                placeholder={i18n.t("memory:custom.namePlaceholder")}
                maxLength={80}
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
            <label className="field">
              <span>{i18n.t("memory:custom.audience")}</span>
              <select
                aria-label={i18n.t("memory:custom.audience")}
                value={audience}
                onChange={(event) =>
                  setAudience(
                    event.target.value === "shared" ? "shared" : "personal",
                  )
                }
              >
                <option value="personal">
                  {i18n.t("memory:audience.personal")}
                </option>
                <option value="shared">
                  {i18n.t("memory:audience.session")}
                </option>
              </select>
            </label>
            <button
              type="submit"
              disabled={!id || !name.trim() || Object.hasOwn(profiles, id)}
            >
              {i18n.t("memory:custom.add")}
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
              <div className="memory-profile-row" key={key}>
                <div className="field">
                  <input
                    aria-label={i18n.t("memory:custom.displayNameOf", { key })}
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
                  <small className="muted">
                    <code>{key}</code> ·{" "}
                    {profile.audience === "shared"
                      ? i18n.t("memory:audience.session")
                      : i18n.t("memory:audience.personal")}{" "}
                    · {i18n.t("memory:sessionCount", { count })}
                  </small>
                </div>
                <button
                  type="button"
                  disabled={count > 0 || usedByAdapter}
                  title={i18n.t("memory:custom.removeHint")}
                  onClick={() => {
                    const next = { ...custom };
                    delete next[key];
                    setConfig({
                      ...config,
                      memory: { ...memory, profiles: next },
                    });
                  }}
                >
                  {i18n.t("memory:custom.remove")}
                </button>
              </div>
            );
          })}
        </section>
        <section className="memory-settings-section">
          <h3>{i18n.t("memory:bindings.title")}</h3>
          <div className="memory-settings-fields">
            <SessionPicker
              client={client}
              adapters={config.adapters}
              suggestions={[
                currentSession,
                ...draftSessions,
                ...Object.keys(bindings),
              ]}
              session={session}
              onChange={setSession}
            />
            <label className="field">
              <span className="field-name">
                {i18n.t("memory:bindings.store")}
              </span>
              <select
                disabled={!isMainSession(session)}
                value={bindings[session] ?? ""}
                onChange={(event) => bind(event.target.value)}
              >
                <option value="">
                  {personal
                    ? i18n.t("memory:bindings.useDefault")
                    : i18n.t("memory:bindings.none")}
                </option>
                {bindings[session] &&
                  !eligible.some(([key]) => key === bindings[session]) && (
                    <option value={bindings[session]} disabled>
                      {i18n.t("memory:bindings.invalid", {
                        store: bindings[session],
                      })}
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
          {Object.keys(bindings).length > 0 && (
            <div
              className="memory-binding-list"
              role="group"
              aria-label={i18n.t("memory:bindings.list")}
            >
              {Object.entries(bindings).map(([key, value]) => (
                <button
                  key={key}
                  type="button"
                  aria-pressed={session === key}
                  title={key}
                  onClick={() => setSession(key)}
                >
                  <span>{sessionTitle(key)}</span>
                  <small>{profiles[value]?.name ?? value}</small>
                </button>
              ))}
            </div>
          )}
        </section>
      </div>
    </section>
  );
}

export function MemoryPage(props: SessionConfigPageProps) {
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
        <h2>{i18n.t("memory:browse.title")}</h2>
        <div className="memory-browser-filters">
          <select
            aria-label={i18n.t("memory:browse.store")}
            value={profile}
            onChange={(event) => {
              setProfile(event.target.value);
              setPage(1);
              setOpen("");
            }}
          >
            {(
              profiles.data ?? [
                { id: "default", name: i18n.t("memory:profiles.default") },
              ]
            ).map((row) => (
              <option key={row.id} value={row.id}>
                {row.name}
              </option>
            ))}
          </select>
          <div
            className="range-tabs"
            role="group"
            aria-label={i18n.t("memory:browse.kind")}
          >
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
                {tx(label)}
              </button>
            ))}
          </div>
        </div>
        <p className="muted">
          {profiles.data?.find((row) => row.id === profile)?.audience ===
          "shared"
            ? i18n.t("memory:browse.sessionHint")
            : i18n.t("memory:browse.ownerHint")}
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
            aria-label={i18n.t("memory:search.label")}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={i18n.t("memory:search.placeholder")}
          />
          <button type="submit">
            <Search size={16} />
            {i18n.t("memory:search.submit")}
          </button>
        </form>
        {memories.error && (
          <QueryErrorNotice
            message={i18n.t("memory:loadFailed", {
              error: memories.error.message,
            })}
          />
        )}
        {memories.data?.items.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <span className="tag">
                {row.id} · {tx(STATUS[row.status] ?? row.status)}
              </span>
              <button
                type="button"
                aria-expanded={open === row.id}
                onClick={() => setOpen(open === row.id ? "" : row.id)}
              >
                {open === row.id
                  ? i18n.t("memory:collapse")
                  : i18n.t("memory:details")}
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
                {detail.isFetching && (
                  <LoadingBar label={i18n.t("memory:loadingDetail")} />
                )}
                {detail.error && (
                  <QueryErrorNotice
                    message={i18n.t("memory:detailFailed", {
                      error: detail.error.message,
                    })}
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
                        <h4>{i18n.t("memory:history")}</h4>
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
                          {i18n.t("memory:evidenceSource", {
                            source: item.source,
                            recorded: item.recorded,
                          })}
                        </small>
                      </blockquote>
                    ))}
                    {detail.data.resolution && (
                      <p className="muted">
                        {i18n.t("memory:resolution", {
                          text: detail.data.resolution,
                        })}
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
              text={query ? i18n.t("memory:noMatch") : i18n.t("memory:empty")}
            />
          )}
        {memories.isPending && <LoadingBar label={i18n.t("memory:loading")} />}
        <Pagination
          label={i18n.t("memory:pages")}
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
