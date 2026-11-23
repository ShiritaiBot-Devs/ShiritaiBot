import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useQuery } from "@tanstack/react-query";
import { Details } from "../components/Details";
import { EmptyState } from "../components/EmptyState";
import { InstanceCollection } from "../components/InstanceCollection";
import {
  createSubagent,
  subagentFields,
  subagentSummary,
} from "../config/schema";
import type { ConfigPageProps } from "./common";
import i18n from "../i18n";

const commands = {
  shiritaibot: "",
  codex: "codex",
  claude_code: "claude",
  deepseek_harness: "dsh",
  antigravity: "agy",
  generic: "",
};

export function SubagentsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const runs = useQuery({
    queryKey: ["records", "subagent_run"],
    queryFn: () => client.records("subagent_run"),
    refetchInterval: 4000,
  });
  const hasNative = config.subagents.some(
    (item) => item.kind === "shiritaibot",
  );
  const tools = useQuery({
    queryKey: ["tools", "web:owner"],
    queryFn: () => client.tools("web:owner"),
    enabled: hasNative,
  });
  const skills = useQuery({
    queryKey: ["skills"],
    queryFn: () => client.skills(),
    enabled: hasNative,
  });
  const models = config.models.filter(
    (m) =>
      m.enabled &&
      m.kind !== "embedding" &&
      config.providers.some(
        (p) => p.id === m.provider && p.enabled && p.protocol !== "systemone",
      ),
  );

  return (
    <>
      <InstanceCollection
        title={i18n.t("subagents:instances")}
        items={config.subagents}
        create={() => createSubagent(config.subagents)}
        onChange={(items) => {
          const subagents = items.map((item) => {
            const previous = config.subagents.find((row) => row.id === item.id);
            if (
              previous &&
              previous.kind !== item.kind &&
              (item.kind === "shiritaibot" || previous.kind === "shiritaibot")
            )
              return {
                ...item,
                model: "",
                command: commands[item.kind] ? [commands[item.kind]] : [],
              };
            if (
              previous &&
              previous.kind !== item.kind &&
              commands[item.kind] &&
              (item.command.length === 0 ||
                (item.command.length === 1 &&
                  item.command[0] === commands[previous.kind]))
            )
              return { ...item, command: [commands[item.kind]] };
            return item;
          });
          setConfig({ ...config, subagents });
        }}
        fields={(item) =>
          subagentFields(
            item,
            models.map((m) => m.id),
            (tools.data?.tools ?? [])
              .map((t) => t.name)
              .filter((name) => !/^(subagent_|skill_)/.test(name)),
            (skills.data ?? [])
              .filter((s) => config.skills.enabled && s.enabled && !s.error)
              .map((s) => s.name),
          )
        }
        subtitle={subagentSummary}
        emptyText={i18n.t("subagents:empty")}
        renderExtra={(item) =>
          item.kind === "shiritaibot" && (
            <>
              {tools.error && (
                <QueryErrorNotice
                  message={i18n.t("subagents:toolsFailed", {
                    error: tools.error.message,
                  })}
                />
              )}
              {skills.error && (
                <QueryErrorNotice
                  message={i18n.t("subagents:skillsFailed", {
                    error: skills.error.message,
                  })}
                />
              )}
            </>
          )
        }
        renderActions={(item) =>
          item.kind !== "shiritaibot" && (
            <button
              type="button"
              onClick={() =>
                void act(
                  () => client.detectSubagent(item.id),
                  i18n.t("subagents:detected"),
                )
              }
            >
              {i18n.t("subagents:detect")}
            </button>
          )
        }
      />
      <section className="panel">
        <h2>{i18n.t("subagents:runs.title")}</h2>
        {runs.error && (
          <QueryErrorNotice
            message={i18n.t("subagents:runs.failed", {
              error: runs.error.message,
            })}
          />
        )}
        {runs.data?.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>{row.instance}</strong>
              <span className="tag">{row.status}</span>
            </div>
            {row.status === "needs_input" && row.question && (
              <p>
                <strong>{i18n.t("subagents:runs.waiting")}</strong>
                {row.question}
              </p>
            )}
            <p>{row.result || row.error || row.workspace}</p>
            {(row.status === "running" || row.status === "queued") && (
              <button
                type="button"
                onClick={() =>
                  void act(
                    () => client.cancelSubagent(row.id),
                    i18n.t("subagents:runs.stopRequested"),
                  )
                }
              >
                {i18n.t("subagents:runs.stop")}
              </button>
            )}
            <Details value={row} />
          </article>
        ))}
        {!runs.data?.length && !runs.error && (
          <EmptyState text={i18n.t("subagents:runs.empty")} />
        )}
      </section>
    </>
  );
}
