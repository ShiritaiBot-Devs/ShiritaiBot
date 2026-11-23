import { LoadingBar } from "../components/LoadingBar";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { EmptyState } from "../components/EmptyState";
import { formatTime } from "../lib/utils";
import type { PageProps } from "./common";
import i18n, { tx } from "../i18n";

const LINE_OPTIONS = [200, 500, 1000, 2000];
const LEVEL_OPTIONS = [
  ["", "logs:filter.all"],
  ["log", "logs:filter.log"],
  ["warning", "logs:filter.warning"],
  ["error", "logs:filter.error"],
] as const;
const RECORD_LEVELS = [
  ["debug", "logs:record.debug"],
  ["info", "logs:record.info"],
  ["warning", "WARNING"],
  ["error", "ERROR"],
] as const;

function RecordLevel({ client, act }: PageProps) {
  const queryClient = useQueryClient();
  const current = useQuery({
    queryKey: ["log-level"],
    queryFn: () => client.logLevel(),
  });
  return (
    <label className="raw-log-follow" title={i18n.t("logs:record.hint")}>
      {i18n.t("logs:record.label")}
      <select
        aria-label={i18n.t("logs:record.label")}
        value={current.data?.level ?? "info"}
        disabled={!current.data}
        onChange={(event) => {
          const level = event.target.value;
          void act(async () => {
            queryClient.setQueryData(
              ["log-level"],
              await client.setLogLevel(level),
            );
          }, i18n.t("logs:record.updated"));
        }}
      >
        {RECORD_LEVELS.map(([value, label]) => (
          <option key={value} value={value}>
            {tx(label)}
          </option>
        ))}
      </select>
    </label>
  );
}

type Tab = "runtime" | "activity" | "raw";

function RunLog({ client, act }: PageProps) {
  const [limit, setLimit] = useState(500);
  const [level, setLevel] = useState("");
  const [source, setSource] = useState("");
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const viewer = useRef<HTMLDivElement>(null);
  const feed = useQuery({
    queryKey: ["logs", limit, level, source, query],
    queryFn: () => client.logs({ limit, level, source, query }),
    refetchInterval: follow ? 2000 : false,
  });
  const entries = feed.data?.entries ?? [];
  const sources = feed.data?.sources ?? [];
  const last = entries.length ? entries[entries.length - 1].at : "";
  useEffect(() => {
    if (follow && viewer.current) {
      viewer.current.scrollTop = viewer.current.scrollHeight;
    }
  }, [last, entries.length, follow]);
  return (
    <>
      <div className="inline raw-log-toolbar">
        <RecordLevel client={client} act={act} />
        <input
          aria-label={i18n.t("logs:runtime.filter")}
          placeholder={i18n.t("logs:filterPlaceholder")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          aria-label={i18n.t("logs:filter.label")}
          value={level}
          onChange={(event) => setLevel(event.target.value)}
        >
          {LEVEL_OPTIONS.map(([value, label]) => (
            <option key={value} value={value}>
              {tx(label)}
            </option>
          ))}
        </select>
        <select
          aria-label={i18n.t("logs:source.label")}
          value={source}
          onChange={(event) => setSource(event.target.value)}
        >
          <option value="">{i18n.t("logs:source.all")}</option>
          {[...new Set([...sources, ...(source ? [source] : [])])].map(
            (name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ),
          )}
        </select>
        <select
          aria-label={i18n.t("logs:count")}
          value={limit}
          onChange={(event) => setLimit(Number(event.target.value))}
        >
          {LINE_OPTIONS.map((value) => (
            <option key={value} value={value}>
              {i18n.t("logs:recentEntries", { count: value })}
            </option>
          ))}
        </select>
        <label className="raw-log-follow">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          {i18n.t("logs:follow")}
        </label>
      </div>
      {feed.isPending && <LoadingBar label={i18n.t("logs:loading")} />}
      {feed.error && (
        <QueryErrorNotice
          message={i18n.t("logs:runtime.failed", { error: feed.error.message })}
        />
      )}
      {entries.length ? (
        <div
          className="run-log"
          ref={viewer}
          aria-label={i18n.t("logs:runtime.title")}
        >
          {entries.map((entry, index) => (
            <div
              className={`run-log-row level-${entry.level.toLowerCase()}`}
              key={`${entry.at}-${index}`}
            >
              <time dateTime={entry.at}>{formatTime(entry.at)}</time>
              <span className="run-log-level">{entry.level}</span>
              <span className="run-log-source">[{entry.source}]</span>
              {entry.tags.map((tag, position) => (
                <span className="run-log-tag" key={position}>
                  [{tag}]
                </span>
              ))}
              <span className="run-log-message">{entry.message}</span>
            </div>
          ))}
        </div>
      ) : (
        !feed.error &&
        !feed.isPending && <EmptyState text={i18n.t("logs:runtime.empty")} />
      )}
      <p className="muted raw-log-hint">{i18n.t("logs:runtime.hint")}</p>
    </>
  );
}

function ActivityLog({ client }: Pick<PageProps, "client">) {
  const activity = useQuery({
    queryKey: ["records", "activity"],
    queryFn: () => client.records("activity"),
    refetchInterval: 4000,
  });
  return (
    <>
      {activity.isPending && <LoadingBar label={i18n.t("logs:loading")} />}
      {activity.error && (
        <QueryErrorNotice
          message={i18n.t("logs:activity.failed", {
            error: activity.error.message,
          })}
        />
      )}
      {activity.data?.map((row) => (
        <div className="log-row" key={row.id}>
          <time>{formatTime(row.at)}</time>
          <span className="tag">{row.kind}</span>
          <span>{row.detail}</span>
        </div>
      ))}
      {!activity.data?.length && !activity.error && !activity.isPending && (
        <EmptyState />
      )}
    </>
  );
}

function RawLog({ client }: Pick<PageProps, "client">) {
  const [lines, setLines] = useState(500);
  const [filter, setFilter] = useState("");
  const [follow, setFollow] = useState(true);
  const viewer = useRef<HTMLPreElement>(null);
  const raw = useQuery({
    queryKey: ["raw-logs", lines, filter],
    queryFn: () => client.rawLogs(lines, filter),
    refetchInterval: follow ? 3000 : false,
  });
  const text = raw.data?.lines.join("\n") ?? "";
  useEffect(() => {
    if (follow && viewer.current) {
      viewer.current.scrollTop = viewer.current.scrollHeight;
    }
  }, [text, follow]);
  return (
    <>
      <div className="inline raw-log-toolbar">
        <input
          aria-label={i18n.t("logs:raw.filter")}
          placeholder={i18n.t("logs:filterPlaceholder")}
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <select
          aria-label={i18n.t("logs:raw.lines")}
          value={lines}
          onChange={(event) => setLines(Number(event.target.value))}
        >
          {LINE_OPTIONS.map((value) => (
            <option key={value} value={value}>
              {i18n.t("logs:recentLines", { count: value })}
            </option>
          ))}
        </select>
        <label className="raw-log-follow">
          <input
            type="checkbox"
            checked={follow}
            onChange={(event) => setFollow(event.target.checked)}
          />
          {i18n.t("logs:raw.autoScroll")}
        </label>
        <button type="button" onClick={() => void raw.refetch()}>
          {i18n.t("logs:raw.refresh")}
        </button>
      </div>
      {raw.isPending && <LoadingBar label={i18n.t("logs:loading")} />}
      {raw.error && (
        <QueryErrorNotice
          message={i18n.t("logs:raw.failed", { error: raw.error.message })}
        />
      )}
      {text ? (
        <pre
          className="raw-log"
          ref={viewer}
          aria-label={i18n.t("logs:raw.title")}
        >
          {text}
        </pre>
      ) : (
        !raw.error &&
        !raw.isPending && <EmptyState text={i18n.t("logs:raw.empty")} />
      )}
      <p className="muted raw-log-hint">{i18n.t("logs:raw.hint")}</p>
    </>
  );
}

export function LogsPage({ client, act }: PageProps) {
  const [tab, setTab] = useState<Tab>("runtime");
  return (
    <section className="panel">
      <div className="panel-head logs-head">
        <h2>
          {tab === "runtime"
            ? i18n.t("logs:runtime.title")
            : tab === "activity"
              ? i18n.t("logs:activity.title")
              : i18n.t("logs:raw.rollingTitle")}
        </h2>
        <div
          className="range-tabs"
          role="group"
          aria-label={i18n.t("logs:tabs")}
        >
          {(
            [
              ["runtime", i18n.t("logs:runtime.title")],
              ["activity", i18n.t("logs:activity.title")],
              ["raw", i18n.t("logs:raw.title")],
            ] as const
          ).map(([value, label]) => (
            <button
              type="button"
              key={value}
              className={tab === value ? "selected" : ""}
              aria-pressed={tab === value}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {tab === "runtime" && <RunLog client={client} act={act} />}
      {tab === "activity" && <ActivityLog client={client} />}
      {tab === "raw" && <RawLog client={client} />}
    </section>
  );
}
