import { LoadingBar } from "../components/LoadingBar";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { AdapterConfig } from "../api/types";
import { FeishuQrDialog } from "../components/FeishuQrDialog";
import { InstanceCollection } from "../components/InstanceCollection";
import { adapterFields, createAdapter } from "../config/schema";
import { useNotice } from "../hooks/useNotice";
import { uniqueId } from "../lib/utils";
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

type AdapterType = NonNullable<AdapterConfig["type"]>;

function AddAdapterDialog({
  onClose,
  onManual,
  onQr,
}: {
  onClose: () => void;
  onManual: (type: AdapterType) => void;
  onQr: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();
  const [type, setType] = useState<AdapterType>("onebot11");

  useEffect(() => {
    const element = dialog.current!;
    element.showModal();
    return () => element.close();
  }, []);

  return (
    <dialog
      className="adapter-dialog"
      ref={dialog}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <h2 id={title}>添加机器人</h2>
      <label className="field">
        <span className="field-name">适配器类型</span>
        <select
          value={type}
          onChange={(event) =>
            setType(event.target.value === "feishu" ? "feishu" : "onebot11")
          }
        >
          <option value="onebot11">QQ · OneBot V11</option>
          <option value="feishu">飞书应用机器人</option>
        </select>
      </label>
      <div className="actions adapter-dialog-actions">
        <button type="button" onClick={onClose}>
          取消
        </button>
        {type === "feishu" ? (
          <>
            <button type="button" onClick={() => onManual(type)}>
              手动配置
            </button>
            <button type="button" className="primary" onClick={onQr}>
              扫码一键创建
            </button>
          </>
        ) : (
          <button
            type="button"
            className="primary"
            onClick={() => onManual(type)}
          >
            继续
          </button>
        )}
      </div>
    </dialog>
  );
}

export function AdaptersPage({
  client,
  config,
  setConfig,
  dirty = false,
}: ConfigPageProps & { dirty?: boolean }) {
  const [creation, setCreation] = useState<"choose" | "qr" | null>(null);
  const [selection, setSelection] = useState<{ id: string; key: number }>();
  const [pendingQrSelection, setPendingQrSelection] = useState<string>();
  const queryClient = useQueryClient();
  const { succeed, warn } = useNotice();
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

  const selectAdded = useCallback((id: string) => {
    setSelection((previous) => ({ id, key: (previous?.key ?? 0) + 1 }));
  }, []);

  useEffect(() => {
    if (
      pendingQrSelection &&
      config.adapters.some((adapter) => adapter.id === pendingQrSelection)
    ) {
      selectAdded(pendingQrSelection);
      setPendingQrSelection(undefined);
    }
  }, [config.adapters, pendingQrSelection, selectAdded]);

  function addManually(type: AdapterType) {
    const adapter = createAdapter(items, type);
    setConfig({ ...config, adapters: [...items, adapter] });
    selectAdded(adapter.id);
    setCreation(null);
  }

  function openQr() {
    if (dirty) {
      setCreation(null);
      warn("请先保存或放弃未保存的配置，再创建飞书机器人。");
      return;
    }
    setCreation("qr");
  }

  const onQrSaved = useCallback(
    async (id: string, enabled: boolean) => {
      setCreation(null);
      setPendingQrSelection(id);
      await Promise.all(
        ["config", "health", "diagnostics"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] }),
        ),
      );
      succeed(
        enabled ? "飞书实例已保存，正在连接。" : "飞书实例已保存为未启用。",
      );
    },
    [queryClient, succeed],
  );

  return (
    <>
      <InstanceCollection<AdapterConfig>
        key={selection?.key}
        title="适配器实例"
        items={items}
        initialSelectedId={selection?.id}
        onAdd={() => setCreation("choose")}
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
                {savedConnection && item.enabled && health.isPending ? (
                  <LoadingBar compact label="读取连接状态" />
                ) : (
                  label
                )}
              </span>
              {diagnostic && <p>{diagnostic}</p>}
            </div>
          );
        }}
      />
      {creation === "choose" && (
        <AddAdapterDialog
          onClose={() => setCreation(null)}
          onManual={addManually}
          onQr={openQr}
        />
      )}
      {creation === "qr" && (
        <FeishuQrDialog
          client={client}
          initialId={uniqueId("feishu", items)}
          canEnable={
            !items.some((item) => item.type === "feishu" && item.enabled)
          }
          onClose={() => setCreation(null)}
          onSaved={onQrSaved}
        />
      )}
    </>
  );
}
