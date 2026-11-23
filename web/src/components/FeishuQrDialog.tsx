import { LoadingBar } from "./LoadingBar";
import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { FeishuQrAdapter, FeishuQrRegistration } from "../api/types";
import i18n, { tx } from "../i18n";

interface FeishuQrDialogProps {
  client: ApiClient;
  initialId: string;
  canEnable: boolean;
  onClose: () => void;
  onSaved: (id: string, enabled: boolean) => Promise<void>;
}

const PLATFORM_NAMES = {
  get feishu() {
    return i18n.t("feishu:platform.feishu");
  },
  lark: "Lark",
};
const TERMINAL_LABELS = {
  applied: "feishu:terminal.applied",
  denied: "feishu:terminal.denied",
  expired: "feishu:terminal.expired",
  error: "feishu:terminal.error",
};

export function FeishuQrDialog({
  client,
  initialId,
  canEnable,
  onClose,
  onSaved,
}: FeishuQrDialogProps) {
  const title = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const active = useRef(false);
  const sessionId = useRef<string | null>(null);
  const applying = useRef(false);
  const [domain, setDomain] =
    useState<FeishuQrRegistration["domain"]>("feishu");
  const [started, setStarted] = useState<FeishuQrRegistration | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [adapter, setAdapter] = useState<FeishuQrAdapter>({
    adapter_id: initialId,
    enabled: canEnable,
    memory_profile: "feishu",
  });
  const status = useQuery({
    queryKey: ["feishu-qr", started?.id],
    initialData: started ?? undefined,
    enabled: started !== null && !busy,
    refetchOnWindowFocus: false,
    gcTime: 0,
    retry: 1,
    refetchInterval: ({ state }) =>
      state.data?.status === "waiting" || state.data?.status === "authorized"
        ? 2000
        : false,
    queryFn: async () => {
      try {
        return await client.feishuQrStatus(started!.id);
      } catch (failure) {
        if (failure instanceof ApiError && failure.status === 404) {
          return { ...started!, status: "expired" as const, qr_url: undefined };
        }
        throw failure;
      }
    },
  });
  const registration = status.data;
  const stage = registration?.status;
  const waiting = stage === "waiting";
  const authorized = stage === "authorized";
  const terminal = stage && stage !== "waiting" && stage !== "authorized";

  useEffect(() => {
    if (
      !busy &&
      sessionId.current &&
      registration?.status === "applied" &&
      registration.adapter_id
    ) {
      sessionId.current = null;
      void onSaved(registration.adapter_id, adapter.enabled && canEnable);
    }
  }, [registration, onSaved, adapter.enabled, canEnable, busy]);

  useEffect(() => {
    const element = dialog.current!;
    active.current = true;
    element.showModal();
    return () => {
      active.current = false;
      element.close();
      if (sessionId.current && !applying.current) {
        void client.cancelFeishuQr(sessionId.current).catch(() => {});
      }
    };
  }, [client]);

  async function start() {
    setError("");
    setBusy(true);
    const previous = sessionId.current;
    try {
      if (previous) await client.cancelFeishuQr(previous);
      if (!active.current) return;
      sessionId.current = null;
      setStarted(null);
      const session = await client.startFeishuQr(domain);
      if (!active.current) {
        void client.cancelFeishuQr(session.id).catch(() => {});
        return;
      }
      sessionId.current = session.id;
      setStarted(session);
    } catch (failure) {
      if (active.current)
        setError(
          failure instanceof Error
            ? failure.message
            : i18n.t("feishu:qrFailed"),
        );
    } finally {
      if (active.current) setBusy(false);
    }
  }

  async function save() {
    if (!registration || !authorized) return;
    applying.current = true;
    setError("");
    setBusy(true);
    try {
      const enabled = adapter.enabled && canEnable;
      const result = await client.applyFeishuQr(registration.id, {
        ...adapter,
        adapter_id: adapter.adapter_id.trim(),
        enabled,
      });
      sessionId.current = null;
      await onSaved(result.adapter_id, enabled);
    } catch (failure) {
      if (active.current)
        setError(
          failure instanceof Error
            ? failure.message
            : i18n.t("feishu:saveFailed"),
        );
    } finally {
      applying.current = false;
      if (active.current) setBusy(false);
    }
  }

  return (
    <dialog
      className="adapter-dialog"
      ref={dialog}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!applying.current) onClose();
      }}
    >
      <h2 id={title}>{i18n.t("feishu:title")}</h2>
      <p className="muted">{i18n.t("feishu:hint")}</p>
      {!authorized && (
        <label className="field">
          <span className="field-name">{i18n.t("feishu:platformLabel")}</span>
          <select
            value={domain}
            disabled={busy || waiting}
            onChange={(event) =>
              setDomain(event.target.value === "lark" ? "lark" : "feishu")
            }
          >
            <option value="feishu">{i18n.t("feishu:platform.feishu")}</option>
            <option value="lark">Lark</option>
          </select>
        </label>
      )}
      {waiting && registration?.qr_url && (
        <div className="feishu-qr-code">
          <QRCodeSVG
            value={registration.qr_url}
            size={196}
            marginSize={4}
            title={i18n.t("feishu:qrLabel")}
          />
          <p role="status">{i18n.t("feishu:waiting")}</p>
          <a
            href={registration.qr_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            {i18n.t("feishu:openAuth")}
          </a>
        </div>
      )}
      {registration?.status === "authorized" && (
        <>
          <p role="status">
            {i18n.t("feishu:created", {
              platform: PLATFORM_NAMES[registration.domain],
            })}{" "}
            <code>{registration.app_id}</code>
          </p>
          <div className="field-grid">
            <label className="field">
              <span className="field-name">{i18n.t("feishu:instanceId")}</span>
              <input
                value={adapter.adapter_id}
                maxLength={64}
                disabled={busy}
                onChange={(event) =>
                  setAdapter({ ...adapter, adapter_id: event.target.value })
                }
              />
            </label>
            <label className="field">
              <span className="field-name">
                {i18n.t("feishu:memoryProfile")}
              </span>
              <select
                value={adapter.memory_profile}
                disabled={busy}
                onChange={(event) =>
                  setAdapter({
                    ...adapter,
                    memory_profile:
                      event.target.value === "default" ? "default" : "feishu",
                  })
                }
              >
                <option value="feishu">{i18n.t("feishu:memory.feishu")}</option>
                <option value="default">
                  {i18n.t("feishu:memory.default")}
                </option>
              </select>
            </label>
            <label className="field feishu-qr-enable full">
              <input
                type="checkbox"
                checked={adapter.enabled && canEnable}
                disabled={busy || !canEnable}
                onChange={(event) =>
                  setAdapter({ ...adapter, enabled: event.target.checked })
                }
              />
              {i18n.t("feishu:enableAfterSave")}
            </label>
          </div>
          {!canEnable && (
            <p className="muted">{i18n.t("feishu:alreadyEnabled")}</p>
          )}
          {adapter.enabled && canEnable && (
            <p className="muted">{i18n.t("feishu:bindOwner")}</p>
          )}
        </>
      )}
      {terminal && (
        <p role="status">
          {registration.message || tx(TERMINAL_LABELS[stage])}
        </p>
      )}
      {status.isError && !terminal && (
        <p role="alert">{i18n.t("feishu:statusRetry")}</p>
      )}
      {error && (
        <p className="feishu-qr-error" role="alert">
          {error}
        </p>
      )}
      <div className="actions adapter-dialog-actions">
        <button type="button" onClick={onClose} disabled={busy && authorized}>
          {i18n.t("feishu:cancel")}
        </button>
        {authorized ? (
          <button
            type="button"
            className="primary"
            disabled={
              busy || !/^[A-Za-z0-9_-]{1,64}$/.test(adapter.adapter_id.trim())
            }
            onClick={() => void save()}
          >
            {busy ? (
              <LoadingBar compact label={i18n.t("feishu:saving")} />
            ) : adapter.enabled && canEnable ? (
              i18n.t("feishu:saveConnect")
            ) : (
              i18n.t("feishu:saveDisabled")
            )}
          </button>
        ) : !waiting && stage !== "applied" ? (
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void start()}
          >
            {busy ? (
              <LoadingBar compact label={i18n.t("feishu:generating")} />
            ) : terminal ? (
              i18n.t("feishu:regenerate")
            ) : (
              i18n.t("feishu:generate")
            )}
          </button>
        ) : null}
      </div>
    </dialog>
  );
}
