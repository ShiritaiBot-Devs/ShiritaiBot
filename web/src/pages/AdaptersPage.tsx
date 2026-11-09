import { useQuery } from "@tanstack/react-query";
import type { AdapterConfig } from "../api/types";
import { InstanceCollection } from "../components/InstanceCollection";
import { adapterFields, createAdapter } from "../config/schema";
import type { ConfigPageProps } from "./common";

const CONNECTION_LABELS: Record<string, string> = {
  connected: "已连接",
  connecting: "连接中",
  disconnected: "未连接",
  reconnecting: "重新连接中",
  error: "连接异常",
};

function savedFeishuIdentity(current: AdapterConfig, saved?: AdapterConfig) {
  return (
    current.type === "feishu" &&
    saved?.type === "feishu" &&
    current.app_id === saved.app_id &&
    (current.domain ?? "feishu") === (saved.domain ?? "feishu")
  );
}

export function AdaptersPage({ client, config, setConfig }: ConfigPageProps) {
  const { data: savedConfig } = useQuery({
    queryKey: ["config"],
    queryFn: () => client.config(),
  });
  const health = useQuery({
    queryKey: ["health"],
    queryFn: () => client.health(),
  });
  const items = config.adapters.map<AdapterConfig>((item) => ({
    ...item,
    type: item.type ?? "onebot11",
    domain: item.domain ?? "feishu",
    wake_keywords: item.wake_keywords ?? ["小知"],
    memory_profile: item.memory_profile ?? "feishu",
  }));

  function savedAdapter(item: AdapterConfig) {
    return savedConfig?.adapters.find((saved) => saved.id === item.id);
  }

  return (
    <InstanceCollection<AdapterConfig>
      title="适配器实例"
      items={items}
      create={() => createAdapter(config.adapters)}
      onChange={(adapters) => {
        setConfig({
          ...config,
          adapters: adapters.map((item) => {
            const previous = items.find((row) => row.id === item.id);
            if (
              item.app_secret &&
              previous &&
              (item.type !== previous.type ||
                item.app_id !== previous.app_id ||
                item.domain !== previous.domain)
            ) {
              return { ...item, app_secret: "" };
            }
            return item;
          }),
        });
      }}
      fields={(item) => {
        const saved = savedAdapter(item);
        return adapterFields(
          item,
          savedFeishuIdentity(item, saved) &&
            Boolean(saved?.app_secret_configured),
        );
      }}
      subtitle={(item) =>
        item.type === "feishu"
          ? item.domain === "lark"
            ? "Lark 应用机器人"
            : "飞书应用机器人"
          : item.url
      }
      emptyText="添加实例，连接 QQ 或飞书。"
      renderStatus={(item) => {
        const saved = savedAdapter(item);
        const status = health.data?.adapters[item.id];
        const isFeishu = item.type === "feishu";
        const savedConnection =
          saved &&
          saved.enabled === item.enabled &&
          (isFeishu
            ? savedFeishuIdentity(item, saved) && !item.app_secret
            : saved.type !== "feishu" &&
              item.url === saved.url &&
              item.token === saved.token);
        const label = !savedConnection
          ? "待保存"
          : !item.enabled
            ? "未启用"
            : health.isPending
              ? "正在读取状态"
              : health.isError
                ? "状态暂不可用"
                : (CONNECTION_LABELS[status ?? ""] ?? status ?? "未连接");
        const diagnostic =
          isFeishu && item.enabled && savedConnection
            ? health.data?.adapter_diagnostics?.[item.id]
            : null;
        return (
          <div className="adapter-connection" role="status">
            <span
              className={
                "tag" +
                (label === CONNECTION_LABELS.connected ? " current" : "")
              }
            >
              {label}
            </span>
            {diagnostic && <p>{diagnostic}</p>}
          </div>
        );
      }}
    />
  );
}
