import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type { FeishuQrAdapter, FeishuQrRegistration } from "../api/types";

interface FeishuQrDialogProps {
  client: ApiClient;
  initialId: string;
  canEnable: boolean;
  onClose: () => void;
  onSaved: (id: string, enabled: boolean) => Promise<void>;
}

const PLATFORM_NAMES = { feishu: "飞书", lark: "Lark" };
const TERMINAL_LABELS = {
  applied: "飞书实例已保存",
  denied: "已取消授权",
  expired: "二维码或授权已过期",
  error: "创建未完成",
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
        setError(failure instanceof Error ? failure.message : "无法生成二维码");
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
        setError(failure instanceof Error ? failure.message : "无法保存机器人");
    } finally {
      applying.current = false;
      if (active.current) setBusy(false);
    }
  }

  return (
    <dialog
      className="feishu-qr-dialog"
      ref={dialog}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!applying.current) onClose();
      }}
    >
      <h2 id={title}>扫码创建飞书机器人</h2>
      <p className="muted">用手机确认创建，再保存到 ShiritaiBot。</p>
      {!authorized && (
        <label className="field">
          <span className="field-name">平台</span>
          <select
            value={domain}
            disabled={busy || waiting}
            onChange={(event) =>
              setDomain(event.target.value === "lark" ? "lark" : "feishu")
            }
          >
            <option value="feishu">飞书</option>
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
            title="飞书创建应用二维码"
          />
          <p role="status">等待手机确认</p>
          <a
            href={registration.qr_url}
            target="_blank"
            rel="noreferrer noopener"
          >
            打开授权页面
          </a>
        </div>
      )}
      {registration?.status === "authorized" && (
        <>
          <p role="status">
            {PLATFORM_NAMES[registration.domain]}应用已创建{" "}
            <code>{registration.app_id}</code>
          </p>
          <div className="field-grid">
            <label className="field">
              <span className="field-name">实例标识</span>
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
              <span className="field-name">记忆库</span>
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
                <option value="feishu">独立飞书记忆库</option>
                <option value="default">默认记忆库</option>
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
              保存后启用连接
            </label>
          </div>
          {!canEnable && (
            <p className="muted">已有启用的飞书连接，新实例将保存为未启用。</p>
          )}
          {adapter.enabled && canEnable && (
            <p className="muted">连接后，按实例提示发送私聊并绑定主人。</p>
          )}
        </>
      )}
      {terminal && (
        <p role="status">{registration.message || TERMINAL_LABELS[stage]}</p>
      )}
      {status.isError && !terminal && (
        <p role="alert">暂时无法读取扫码状态，正在重试。</p>
      )}
      {error && (
        <p className="feishu-qr-error" role="alert">
          {error}
        </p>
      )}
      <div className="actions feishu-qr-actions">
        <button type="button" onClick={onClose} disabled={busy && authorized}>
          取消
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
            {busy
              ? "正在保存…"
              : adapter.enabled && canEnable
                ? "保存并连接"
                : "保存为未启用"}
          </button>
        ) : !waiting && stage !== "applied" ? (
          <button
            type="button"
            className="primary"
            disabled={busy}
            onClick={() => void start()}
          >
            {busy ? "正在生成…" : terminal ? "重新生成二维码" : "生成二维码"}
          </button>
        ) : null}
      </div>
    </dialog>
  );
}
