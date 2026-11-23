import { MetricIcon } from "../components/MetricIcon";
import { LoadingBar } from "../components/LoadingBar";
import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  ArrowUpRight,
  CheckCircle2,
  Circle,
  Network,
} from "lucide-react";
import type { ApiClient } from "../api/client";
import type { PageId } from "../navigation";
import { ApprovalCard } from "../components/ApprovalCard";
import { EmptyState } from "../components/EmptyState";
import type { Action } from "./common";
import type { NavigationProps } from "./common";
import { Dashboard } from "./Dashboard";
import { formatDateTime } from "../lib/utils";
import i18n from "../i18n";

function FirstRunChecklist({
  client,
  onNavigate,
}: {
  client: ApiClient;
  onNavigate: (page: PageId) => void;
}) {
  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => client.config(),
  });
  const data = config.data;
  if (!data) return null;
  const steps: { label: string; done: boolean; page: PageId }[] = [
    {
      label: i18n.t("overview:setup.provider"),
      done: data.providers.some((provider) => provider.enabled),
      page: "models",
    },
    {
      label: i18n.t("overview:setup.model"),
      done: data.models.some((model) => model.enabled),
      page: "models",
    },
    {
      label: i18n.t("overview:setup.routes"),
      done: ["chat", "agent", "agent_chat"].every(
        (route) => (data.routes[route] ?? []).length > 0,
      ),
      page: "models",
    },
    {
      label: i18n.t("overview:setup.webSearch"),
      done:
        !data.web_search.enabled ||
        Boolean(data.web_search[data.web_search.provider].api_key),
      page: "settings",
    },
    {
      label: i18n.t("overview:setup.adapter"),
      done: data.adapters.some((adapter) => adapter.enabled),
      page: "adapters",
    },
  ];
  if (steps.every((step) => step.done)) return null;
  return (
    <section className="panel">
      <h2>{i18n.t("overview:setup.title")}</h2>
      <p className="muted">{i18n.t("overview:setup.hint")}</p>
      <ul className="checklist">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "done" : undefined}>
            {step.done ? (
              <CheckCircle2
                size={16}
                aria-label={i18n.t("overview:setup.done")}
              />
            ) : (
              <Circle size={16} aria-label={i18n.t("overview:setup.todo")} />
            )}
            <span className={step.done ? "muted" : ""}>{step.label}</span>
            {!step.done && (
              <button
                type="button"
                className="text-button"
                onClick={() => onNavigate(step.page)}
              >
                {i18n.t("overview:setup.go")}
                <ArrowUpRight size={14} />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function OverviewPage({
  client,
  act,
  onNavigate,
}: { client: ApiClient; act: Action } & NavigationProps) {
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client.health(),
    refetchInterval: 10000,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });
  const activity = useQuery({
    queryKey: ["records", "activity"],
    queryFn: () => client.records("activity"),
    refetchInterval: 4000,
  });
  const pending = (approvals.data ?? []).filter(
    (row) => row.status === "pending" && row.reviewer !== "model",
  );
  const adapters = Object.entries(health.data?.adapters ?? {});

  return (
    <>
      <FirstRunChecklist client={client} onNavigate={onNavigate} />
      <div className="metrics">
        {(
          [
            ["adapters", adapters.length],
            ["tools", health.data?.tools.length ?? 0],
            ["approvals", pending.length],
            ["subagents", health.data?.subagents ?? 0],
          ] as const
        ).map(([id, value]) => (
          <div className="metric" key={id}>
            <MetricIcon id={id} />
            <span>{i18n.t(`overview:metrics.${id}.label`)}</span>
            <strong>
              {(id === "approvals" ? approvals.isPending : health.isPending) ? (
                <LoadingBar
                  compact
                  label={i18n.t("common:pagination.loading", {
                    label: i18n.t(`overview:metrics.${id}.label`),
                  })}
                />
              ) : (
                value
              )}
            </strong>
            <small>{i18n.t(`overview:metrics.${id}.note`)}</small>
          </div>
        ))}
      </div>
      {pending.length > 0 && (
        <section className="panel">
          <h2>{i18n.t("overview:pendingTitle", { count: pending.length })}</h2>
          {pending.map((row) => (
            <ApprovalCard
              key={row.id}
              approval={row}
              busy={false}
              onResolve={(allow) =>
                void act(
                  () => client.resolveApproval(row.id, allow),
                  allow
                    ? i18n.t("overview:allowedOnce")
                    : i18n.t("overview:deniedOnce"),
                )
              }
            />
          ))}
        </section>
      )}
      <Dashboard client={client} />
      <div className="two-col">
        <section className="panel">
          <div className="panel-head">
            <h2>{i18n.t("overview:connections.title")}</h2>
            <Network size={17} aria-hidden="true" />
          </div>
          {health.isPending ? (
            <LoadingBar label={i18n.t("overview:connections.loading")} />
          ) : adapters.length ? (
            adapters.map(([id, status]) => (
              <div className="list-row" key={id}>
                <span
                  className={"dot " + (status === "connected" ? "on" : "")}
                  aria-hidden="true"
                />
                <strong>{id}</strong>
                <span className="tag">{String(status)}</span>
              </div>
            ))
          ) : (
            <EmptyState text={i18n.t("overview:connections.empty")} />
          )}
          <button
            type="button"
            className="text-button"
            onClick={() => onNavigate("adapters")}
          >
            {i18n.t("overview:connections.manage")}
            <ArrowUpRight size={14} />
          </button>
        </section>
        <section className="panel">
          <div className="panel-head">
            <h2>{i18n.t("overview:activity.title")}</h2>
            <Activity size={17} aria-hidden="true" />
          </div>
          {activity.isPending ? (
            <LoadingBar label={i18n.t("overview:activity.loading")} />
          ) : activity.data?.length ? (
            activity.data.slice(0, 5).map((row) => (
              <div className="activity-row" key={row.id}>
                <span className="dot on" aria-hidden="true" />
                <div>
                  <strong>{row.kind}</strong>
                  <p>{row.detail}</p>
                </div>
              </div>
            ))
          ) : (
            <EmptyState text={i18n.t("overview:activity.empty")} />
          )}
          <p className="muted activity-time">
            {activity.data?.[0]
              ? i18n.t("overview:activity.latest", {
                  time: formatDateTime(activity.data[0].at),
                })
              : ""}
          </p>
        </section>
      </div>
    </>
  );
}
