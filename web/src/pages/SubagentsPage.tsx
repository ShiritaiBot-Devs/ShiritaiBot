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
        title="子代理实例"
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
        emptyText="添加 ShiritaiBot 内置子代理或外部 CLI 实例。"
        renderExtra={(item) =>
          item.kind === "shiritaibot" && (
            <>
              {tools.error && (
                <QueryErrorNotice
                  message={`读取工具失败：${tools.error.message}`}
                />
              )}
              {skills.error && (
                <QueryErrorNotice
                  message={`读取技能失败：${skills.error.message}`}
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
                void act(() => client.detectSubagent(item.id), "已检测到 CLI")
              }
            >
              检测 CLI
            </button>
          )
        }
      />
      <section className="panel">
        <h2>任务记录</h2>
        {runs.error && (
          <QueryErrorNotice message={`读取任务失败：${runs.error.message}`} />
        )}
        {runs.data?.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>{row.instance}</strong>
              <span className="tag">{row.status}</span>
            </div>
            {row.status === "needs_input" && row.question && (
              <p>
                <strong>等待回复：</strong>
                {row.question}
              </p>
            )}
            <p>{row.result || row.error || row.workspace}</p>
            {(row.status === "running" || row.status === "queued") && (
              <button
                type="button"
                onClick={() =>
                  void act(() => client.cancelSubagent(row.id), "已请求停止")
                }
              >
                停止
              </button>
            )}
            <Details value={row} />
          </article>
        ))}
        {!runs.data?.length && !runs.error && (
          <EmptyState text="在对话中委派任务，审批后会出现在这里。" />
        )}
      </section>
    </>
  );
}
