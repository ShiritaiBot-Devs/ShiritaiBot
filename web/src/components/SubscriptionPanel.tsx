import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { Trans } from "react-i18next";
import type { ApiClient } from "../api/client";
import type { SubscriptionUsage } from "../api/types";
import type { Action } from "../pages/common";
import { QueryErrorNotice } from "./QueryErrorNotice";
import { formatNumber } from "../lib/utils";
import i18n from "../i18n";

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
      <strong>
        {i18n.t("subscription:calls", {
          count: usage.calls,
          calls: number.format(usage.calls),
        })}
      </strong>
      <small>
        {i18n.t("subscription:tokens", {
          input: number.format(usage.input_tokens),
          output: number.format(usage.output_tokens),
        })}
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
    <section
      className="model-section"
      aria-label={i18n.t("subscription:region")}
    >
      <div className="model-card">
        <div className="panel-head">
          <div>
            <h3>{i18n.t("subscription:title")}</h3>
            {details && (
              <p className="muted" role="status">
                {details.status === "connected"
                  ? i18n.t("subscription:connected")
                  : details.status === "reauthorize"
                    ? i18n.t("subscription:expired")
                    : i18n.t("subscription:disconnected")}
                {details.account && " · " + details.account}
              </p>
            )}
          </div>
          <a href={USAGE_URL} target="_blank" rel="noreferrer">
            {i18n.t("subscription:viewUsage")}
          </a>
        </div>
        <div className="subscription-warning" role="note">
          <TriangleAlert size={16} aria-hidden="true" />
          <p>{i18n.t("subscription:accountWarning")}</p>
        </div>
        {query.isError && <QueryErrorNotice message={query.error.message} />}
        {query.data?.saved === false && (
          <p className="muted">{i18n.t("subscription:saveFirst")}</p>
        )}
        {details && (
          <>
            {details.limit_reached && (
              <p role="status">{i18n.t("subscription:limitHit")}</p>
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
                  i18n.t("subscription:redownload")
                ) : (
                  i18n.t("subscription:reconnect")
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
                    }, i18n.t("subscription:disconnectedDone"))
                  }
                >
                  {i18n.t("subscription:disconnect")}
                </button>
              )}
            </div>
            {revokeUnconfirmed && (
              <p role="status">{i18n.t("subscription:revokeHint")}</p>
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
                  }, i18n.t("subscription:authorized"));
                }}
              >
                <p>
                  <Trans
                    ns="subscription"
                    i18nKey="helperSteps"
                    values={{
                      helper: HELPER_NAME,
                      minutes: Math.ceil(expiresIn / 60),
                    }}
                    components={{ code: <code /> }}
                  />
                </p>
                <label>
                  {i18n.t("subscription:callbackUrl")}
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
                  {i18n.t("subscription:finish")}
                </button>
              </form>
            )}
            <h3>{i18n.t("subscription:usage.title")}</h3>
            <div className="subscription-usage">
              <UsagePeriod
                label={i18n.t("subscription:usage.today")}
                usage={details.usage.today}
              />
              <UsagePeriod
                label={i18n.t("subscription:usage.last7")}
                usage={details.usage.last_7_days}
              />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
