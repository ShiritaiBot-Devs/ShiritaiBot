import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { SubscriptionUsage } from "../api/types";
import type { Action } from "../pages/common";
import { QueryErrorNotice } from "./QueryErrorNotice";
import { formatNumber } from "../lib/utils";

const HELPER_NAME = "shiritaibot-chatgpt-login.py";
const USAGE_URL = "https://chatgpt.com/settings/usage";
const number = { format: (value: number) => formatNumber(value) };

function UsagePeriod({
  label,
  usage,
}: {
  label: string;
  usage: SubscriptionUsage;
}) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong>{number.format(usage.calls)} 次请求</strong>
      <small>
        输入 {number.format(usage.input_tokens)} · 输出{" "}
        {number.format(usage.output_tokens)} Token
      </small>
    </div>
  );
}

function downloadHelper(script: string) {
  const url = URL.createObjectURL(
    new Blob([script], { type: "text/x-python;charset=utf-8" }),
  );
  const link = document.createElement("a");
  link.href = url;
  link.download = HELPER_NAME;
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function SubscriptionPanel({
  id,
  client,
  act,
  onConnectionChange,
}: {
  id: string;
  client: ApiClient;
  act: Action;
  onConnectionChange: () => void;
}) {
  const [working, setWorking] = useState(false);
  const [expiresIn, setExpiresIn] = useState(0);
  const [callback, setCallback] = useState("");
  const [revokeUnconfirmed, setRevokeUnconfirmed] = useState(false);
  const query = useQuery({
    queryKey: ["openai-subscription", id],
    queryFn: () => client.subscriptionStatus(id),
    refetchInterval: 30000,
  });
  const details = query.data?.saved ? query.data : null;
  const showSignIn = details && details.status !== "connected" && !expiresIn;

  function run(work: () => Promise<unknown>, success = "") {
    setWorking(true);
    void act(async () => {
      try {
        return await work();
      } finally {
        setWorking(false);
      }
    }, success);
  }

  return (
    <section className="model-section" aria-label="ChatGPT 套餐连接与用量">
      <div className="model-card">
        <div className="panel-head">
          <div>
            <h3>ChatGPT 套餐</h3>
            {details && (
              <p className="muted" role="status">
                {details.status === "connected"
                  ? "已连接，使用 ChatGPT 套餐"
                  : details.status === "reauthorize"
                    ? "授权已失效，请重新连接"
                    : "未连接"}
                {details.account && " · " + details.account}
              </p>
            )}
          </div>
          <a href={USAGE_URL} target="_blank" rel="noreferrer">
            查看 ChatGPT 用量
          </a>
        </div>
        {query.isError && <QueryErrorNotice message={query.error.message} />}
        {query.data?.saved === false && (
          <p className="muted">保存配置后即可连接部署者的 ChatGPT 账号。</p>
        )}
        {details && (
          <>
            {details.limit_reached && (
              <p role="status">
                最近一次调用达到使用上限，请在 ChatGPT 查看适用的限额。
              </p>
            )}
            <div className="inline">
              <button
                type="button"
                data-oai-siwc-button=""
                className={showSignIn ? "subscription-sign-in" : undefined}
                disabled={working}
                onClick={() =>
                  run(async () => {
                    const { script, expires_in } =
                      await client.startSubscription(id);
                    setCallback("");
                    setExpiresIn(expires_in);
                    downloadHelper(script);
                  })
                }
              >
                {showSignIn ? (
                  <>
                    {/* Mark from the OpenAI Sign in with ChatGPT website guide. */}
                    <img
                      src="/chatgpt-sign-in-white.svg"
                      alt=""
                      width={16}
                      height={16}
                    />
                    Continue with ChatGPT
                  </>
                ) : expiresIn ? (
                  "重新下载授权助手"
                ) : (
                  "重新连接"
                )}
              </button>
              {details.status === "connected" && (
                <button
                  type="button"
                  disabled={working}
                  onClick={() =>
                    run(async () => {
                      const { revoked } =
                        await client.disconnectSubscription(id);
                      setExpiresIn(0);
                      setCallback("");
                      setRevokeUnconfirmed(!revoked);
                      onConnectionChange();
                    }, "已断开 ChatGPT 账号")
                  }
                >
                  断开连接
                </button>
              )}
            </div>
            {revokeUnconfirmed && (
              <p role="status">
                已断开本实例。远端撤销未确认，可在 ChatGPT
                用量设置中移除应用授权。
              </p>
            )}
            {!!expiresIn && (
              <form
                className="subscription-auth"
                onSubmit={(event) => {
                  event.preventDefault();
                  run(async () => {
                    const value = callback.trim();
                    setCallback("");
                    await client.finishSubscription(id, value);
                    setExpiresIn(0);
                    setRevokeUnconfirmed(false);
                    onConnectionChange();
                  }, "已授权使用 ChatGPT 套餐");
                }}
              >
                <p>
                  在打开 WebUI 的电脑运行下载的授权助手。Windows 用{" "}
                  <code>py {HELPER_NAME}</code>， macOS / Linux 用{" "}
                  <code>python3 {HELPER_NAME}</code>。完成 ChatGPT
                  登录后，把助手页面显示的回调地址粘贴到这里。授权请求在{" "}
                  {Math.ceil(expiresIn / 60)} 分钟后过期。
                </p>
                <label>
                  一次性回调地址
                  <input
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="http://127.0.0.1:…/auth/callback?…"
                    value={callback}
                    onChange={(event) => setCallback(event.target.value)}
                    maxLength={16000}
                  />
                </label>
                <button type="submit" disabled={working || !callback.trim()}>
                  完成授权
                </button>
              </form>
            )}
            <h3>本实例用量</h3>
            <div className="subscription-usage">
              <UsagePeriod label="今日" usage={details.usage.today} />
              <UsagePeriod label="近 7 天" usage={details.usage.last_7_days} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
