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
import i18n, { tx } from "../i18n";

const CONNECTION_LABELS: Record<string, string> = {
  connected: "adapters:status.connected",
  connecting: "adapters:status.connecting",
  disconnected: "adapters:status.disconnected",
  reconnecting: "adapters:status.reconnecting",
  error: "adapters:status.error",
};

function savedAppIdentity(current: AdapterConfig, saved?: AdapterConfig) {
  return (
    (current.type === "feishu" || current.type === "qq_official") &&
    saved?.type === current.type &&
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
      <h2 id={title}>{i18n.t("adapters:add.title")}</h2>
      <label className="field">
        <span className="field-name">{i18n.t("adapters:add.type")}</span>
        <select
          value={type}
          onChange={(event) => setType(event.target.value as AdapterType)}
        >
          <option value="onebot11">QQ · OneBot V11</option>
          <option value="qq_official">
            {i18n.t("adapters:type.qq_official")}
          </option>
          <option value="feishu">{i18n.t("adapters:type.feishu")}</option>
        </select>
      </label>
      <div className="actions adapter-dialog-actions">
        <button type="button" onClick={onClose}>
          {i18n.t("adapters:cancel")}
        </button>
        {type === "feishu" ? (
          <>
            <button type="button" onClick={() => onManual(type)}>
              {i18n.t("adapters:add.manual")}
            </button>
            <button type="button" className="primary" onClick={onQr}>
              {i18n.t("adapters:add.scan")}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="primary"
            onClick={() => onManual(type)}
          >
            {i18n.t("adapters:add.continue")}
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
    wake_keywords: item.wake_keywords ?? ["小知"], // i18n: ok
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
      warn(i18n.t("adapters:saveFirst"));
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
        enabled
          ? i18n.t("adapters:feishuSaved")
          : i18n.t("adapters:feishuSavedDisabled"),
      );
    },
    [queryClient, succeed],
  );

  return (
    <>
      <InstanceCollection<AdapterConfig>
        key={selection?.key}
        title={i18n.t("adapters:instances")}
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
            savedAppIdentity(item, saved) &&
              Boolean(saved?.app_secret_configured),
          );
        }}
        subtitle={(item) =>
          item.type === "feishu"
            ? item.domain === "lark"
              ? i18n.t("adapters:type.lark")
              : i18n.t("adapters:type.feishu")
            : item.type === "qq_official"
              ? i18n.t("adapters:type.qq_official")
              : item.url
        }
        emptyText={i18n.t("adapters:empty")}
        renderStatus={(item) => {
          const saved = savedAdapter(item);
          const status = health.data?.adapters[item.id];
          const hasAppSecret =
            item.type === "feishu" || item.type === "qq_official";
          const savedConnection =
            saved &&
            saved.enabled === item.enabled &&
            (hasAppSecret
              ? savedAppIdentity(item, saved) &&
                !item.app_secret &&
                Boolean(item.qq_sandbox) === Boolean(saved.qq_sandbox)
              : (saved.type ?? "onebot11") === "onebot11" &&
                item.url === saved.url &&
                item.token === saved.token);
          const label = !savedConnection
            ? i18n.t("adapters:pending")
            : !item.enabled
              ? i18n.t("adapters:disabled")
              : health.isPending
                ? i18n.t("adapters:loadingStatus")
                : health.isError
                  ? i18n.t("adapters:statusUnavailable")
                  : item.type === "qq_official" && status === "connected"
                    ? i18n.t("adapters:credentialsOk")
                    : tx(
                        CONNECTION_LABELS[status ?? ""] ??
                          status ??
                          i18n.t("adapters:status.disconnected"),
                      );
          const diagnostic =
            hasAppSecret && item.enabled && savedConnection
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
                  <LoadingBar
                    compact
                    label={i18n.t("adapters:loadingConnections")}
                  />
                ) : (
                  label
                )}
              </span>
              {diagnostic && <p>{diagnostic}</p>}
              {item.type === "qq_official" && (
                <p>
                  {i18n.t("adapters:callbackPath")}{" "}
                  <code>/api/adapters/qq-official/{item.id}/callback</code>
                </p>
              )}
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
