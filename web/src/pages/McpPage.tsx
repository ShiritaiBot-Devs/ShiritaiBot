import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { InstanceCollection } from "../components/InstanceCollection";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { createMcpServer, mcpFields, mcpSummary } from "../config/schema";
import type { McpServerStatus } from "../api/types";
import type { ConfigPageProps } from "./common";
import i18n, { tx } from "../i18n";

const statuses: Record<McpServerStatus["status"], string> = {
  connected: "mcp:status.connected",
  connecting: "mcp:status.connecting",
  error: "mcp:status.error",
  disabled: "mcp:status.disabled",
};

export function McpPage({ client, config, setConfig, act }: ConfigPageProps) {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["mcp"],
    queryFn: () => client.mcpServers(),
    refetchInterval: 10000,
  });
  const rows = status.data ?? [];

  return (
    <>
      <InstanceCollection
        title={i18n.t("mcp:servers")}
        items={config.mcp_servers}
        create={() => createMcpServer(config.mcp_servers)}
        onChange={(mcp_servers) => setConfig({ ...config, mcp_servers })}
        fields={mcpFields}
        subtitle={mcpSummary}
        emptyText={i18n.t("mcp:hint")}
        renderActions={(item) => (
          <button
            type="button"
            onClick={() =>
              void act(async () => {
                const result = await client.reconnectMcp(item.id);
                queryClient.setQueryData(["mcp"], result);
              }, i18n.t("mcp:reconnected"))
            }
          >
            <RefreshCw size={14} />
            {i18n.t("mcp:reconnect")}
          </button>
        )}
      />
      <section className="panel">
        <h2>{i18n.t("mcp:statusTitle")}</h2>
        <p className="muted">{i18n.t("mcp:statusHint")}</p>
        {status.error && (
          <QueryErrorNotice
            message={i18n.t("mcp:loadFailed", { error: status.error.message })}
          />
        )}
        {rows.map((row) => (
          <article className="record" key={row.id}>
            <div className="panel-head">
              <strong>
                {row.id}
                {row.server.name
                  ? ` · ${row.server.name}${row.server.version ? " " + row.server.version : ""}`
                  : ""}
              </strong>
              <span className="tag">{tx(statuses[row.status])}</span>
            </div>
            {row.error && <p role="alert">{row.error}</p>}
            {row.instructions && <p className="muted">{row.instructions}</p>}
            {row.tools.length > 0 && (
              <ul className="plain-list">
                {row.tools.map((tool) => (
                  <li key={tool.name}>
                    <code>{tool.name}</code>
                    <span className="tag">{tool.risk}</span>
                    {!tool.enabled && (
                      <span className="tag">{i18n.t("mcp:hidden")}</span>
                    )}
                    {tool.read_only_hint && (
                      <span className="tag">{i18n.t("mcp:readOnly")}</span>
                    )}
                    {tool.error && <span role="alert"> {tool.error}</span>}
                    {tool.description && (
                      <small className="muted"> {tool.description}</small>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </article>
        ))}
        {!rows.length && !status.error && (
          <EmptyState text={i18n.t("mcp:empty")} />
        )}
      </section>
    </>
  );
}
