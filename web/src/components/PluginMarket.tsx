import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, ExternalLink, RefreshCw } from "lucide-react";
import type { ApiClient } from "../api/client";
import type {
  IndexText,
  MarketPlugin,
  PluginInfo,
  PluginMarketListing,
  Settings,
} from "../api/types";
import i18n, { currentLocale, normalizeLocale } from "../i18n";
import { EmptyState } from "./EmptyState";
import { LoadingBar } from "./LoadingBar";
import { TextFieldList } from "./TextFieldList";

export const OFFICIAL_STORE = "https://plugin.shiritaibot.dev";
export const OFFICIAL_INDEX = OFFICIAL_STORE + "/api/v1/plugins.json";
const KINDS = new Set([
  "data_sharing",
  "telemetry",
  "external_account",
  "paid_service",
  "file_access",
  "network",
  "system",
  "other",
]);

/** Index text in the interface language, then Chinese, then whatever is there. */
export function pickText(value: IndexText | undefined): string {
  if (!value) return "";
  if (typeof value === "string") return value;
  const entries = Object.entries(value);
  const find = (locale: string) =>
    entries.find(([key]) => normalizeLocale(key) === locale)?.[1];
  return find(currentLocale()) ?? find("zh-CN") ?? entries[0]?.[1] ?? "";
}

function allText(value: IndexText): string {
  return typeof value === "string" ? value : Object.values(value).join(" ");
}

function matches(entry: MarketPlugin, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const haystack = [
    entry.id,
    allText(entry.name),
    allText(entry.description),
    allText(entry.short_description),
    entry.author,
    entry.owner,
    ...entry.tags,
  ]
    .join(" ")
    .toLowerCase();
  return words.every((word) => haystack.includes(word));
}

function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return new Intl.DateTimeFormat(currentLocale(), {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function storeLink(): string {
  return OFFICIAL_STORE + (currentLocale() === "en" ? "/en" : "");
}

function repoName(url: string): string {
  return url.replace(/^https:\/\/github\.com\//, "");
}

function InstallDialog({
  entry,
  working,
  onConfirm,
  onCancel,
}: {
  entry: MarketPlugin;
  working: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const name = pickText(entry.name) || entry.id;
  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={i18n.t("market:confirm.dialog")}
      className="config-editor market-confirm"
    >
      <h3>{i18n.t("market:confirm.title", { name })}</h3>
      <p className="muted">
        {i18n.t("market:confirm.source", {
          repo: repoName(entry.repo),
          commit: entry.commit.slice(0, 12),
          version: entry.version,
          id: entry.id,
        })}
      </p>
      {!entry.compatible && (
        <p role="alert">
          {i18n.t("market:confirm.incompatible", {
            range: entry.shiritaibot_version,
          })}
        </p>
      )}
      <section
        className={
          "market-notices" + (entry.disclosures.length ? " has-notices" : "")
        }
        aria-label={i18n.t("market:confirm.notices")}
      >
        <h4>
          {entry.disclosures.length > 0 && <AlertTriangle size={15} />}
          {i18n.t("market:confirm.notices")}
        </h4>
        {entry.disclosures.length ? (
          <>
            <p>{i18n.t("market:confirm.noticesLead")}</p>
            <ul>
              {entry.disclosures.map((item, index) => (
                <li key={index}>
                  <span className="tag">
                    {i18n.t(
                      `market:kinds.${KINDS.has(item.kind) ? item.kind : "other"}` as "market:kinds.other",
                    )}
                  </span>
                  <span>{pickText(item.text)}</span>
                  {item.location && (
                    <small className="muted">
                      {i18n.t("market:confirm.location", {
                        location: item.location,
                      })}
                    </small>
                  )}
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p>{i18n.t("market:confirm.noNotices")}</p>
        )}
        <p className="muted">{i18n.t("market:confirm.sandbox")}</p>
      </section>
      <div className="actions">
        <button type="button" disabled={working} onClick={onConfirm}>
          <Download size={14} />
          {i18n.t("market:confirm.install")}
        </button>
        <button type="button" disabled={working} onClick={onCancel}>
          {i18n.t("market:confirm.cancel")}
        </button>
      </div>
    </div>
  );
}

export function PluginMarket({
  client,
  config,
  setConfig,
  installed,
  working,
  act,
  onInstall,
}: {
  client: ApiClient;
  config: Settings;
  setConfig: (config: Settings) => void;
  installed: PluginInfo[];
  working: boolean;
  act: (work: () => Promise<void>, message?: string) => Promise<void>;
  onInstall: (entry: MarketPlugin) => Promise<void>;
}) {
  const queryClient = useQueryClient();
  const listing = useQuery({
    queryKey: ["plugin-market"],
    queryFn: () => client.pluginMarket(),
  });
  const [query, setQuery] = useState("");
  const [confirming, setConfirming] = useState<MarketPlugin | null>(null);
  const versions = useMemo(
    () => new Map(installed.map((item) => [item.id, item.version ?? ""])),
    [installed],
  );
  const plugins = listing.data?.plugins ?? [];
  const shown = plugins.filter((entry) => matches(entry, query));
  const sources = config.plugin_market?.sources ?? [OFFICIAL_INDEX];

  async function refresh() {
    await act(async () => {
      const data = await client.pluginMarket(true);
      queryClient.setQueryData<PluginMarketListing>(["plugin-market"], data);
    }, i18n.t("market:refreshed"));
  }

  return (
    <section className="panel" aria-labelledby="plugin-market-title">
      <div className="panel-head">
        <div>
          <h2 id="plugin-market-title">{i18n.t("market:title")}</h2>
          <p className="muted">{i18n.t("market:intro")}</p>
        </div>
        <div className="actions">
          <a
            className="button-link"
            href={storeLink()}
            target="_blank"
            rel="noreferrer"
          >
            <ExternalLink size={14} />
            {i18n.t("market:website")}
          </a>
          <button
            type="button"
            disabled={working || listing.isFetching}
            onClick={() => void refresh()}
          >
            <RefreshCw size={14} />
            {i18n.t("market:refresh")}
          </button>
        </div>
      </div>
      {confirming && (
        <InstallDialog
          entry={confirming}
          working={working}
          onCancel={() => setConfirming(null)}
          onConfirm={() =>
            void onInstall(confirming).then(() => setConfirming(null))
          }
        />
      )}
      {listing.data?.sources.map((source) =>
        source.error ? (
          <p key={source.url} className="market-source-note" role="status">
            {source.fetched_at
              ? i18n.t("market:sources.stale", {
                  url: source.url,
                  error: source.error,
                  time: formatTime(source.fetched_at),
                })
              : i18n.t("market:sources.failed", {
                  url: source.url,
                  error: source.error,
                })}
          </p>
        ) : source.dropped ? (
          <p key={source.url} className="market-source-note" role="status">
            {i18n.t("market:sources.dropped", {
              url: source.url,
              count: source.dropped,
            })}
          </p>
        ) : null,
      )}
      <label className="field market-search">
        <span className="sr-only">{i18n.t("market:search")}</span>
        <input
          type="search"
          aria-label={i18n.t("market:search")}
          placeholder={i18n.t("market:searchPlaceholder")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>
      {listing.isPending ? (
        <LoadingBar label={i18n.t("market:loading")} />
      ) : listing.isError ? (
        <p role="alert">{listing.error.message}</p>
      ) : !plugins.length ? (
        <EmptyState text={i18n.t("market:empty")} />
      ) : !shown.length ? (
        <EmptyState text={i18n.t("market:noMatch", { query })} />
      ) : (
        <>
          <p className="muted">
            {i18n.t("market:count", { count: shown.length })}
          </p>
          <ul className="market-grid">
            {shown.map((entry) => {
              const version = versions.get(entry.id);
              const name = pickText(entry.name) || entry.id;
              return (
                <li key={entry.id} className="market-card">
                  <div className="market-card-head">
                    <strong>{name}</strong>
                    <span className="tag">{entry.version}</span>
                  </div>
                  <code className="muted">{entry.id}</code>
                  <p>
                    {pickText(entry.short_description) ||
                      pickText(entry.description)}
                  </p>
                  <div className="market-card-meta">
                    <span className="muted">
                      {i18n.t("market:by", { author: entry.author })}
                    </span>
                    {entry.disclosures.length > 0 && (
                      <span className="tag market-notice-tag">
                        <AlertTriangle size={12} />
                        {i18n.t("market:notices", {
                          count: entry.disclosures.length,
                        })}
                      </span>
                    )}
                    {!entry.compatible && (
                      <span className="tag market-notice-tag">
                        {i18n.t("market:incompatible", {
                          range: entry.shiritaibot_version,
                        })}
                      </span>
                    )}
                    {entry.tags.map((tag) => (
                      <span key={tag} className="tag">
                        {tag}
                      </span>
                    ))}
                  </div>
                  <div className="actions">
                    {version !== undefined ? (
                      <span className="tag current">
                        {i18n
                          .t("market:installedTag", {
                            version: version
                              ? "v" + version.replace(/^v/, "")
                              : "",
                          })
                          .trim()}
                      </span>
                    ) : (
                      <button
                        type="button"
                        disabled={working}
                        onClick={() => setConfirming(entry)}
                      >
                        <Download size={14} />
                        {i18n.t("market:install")}
                      </button>
                    )}
                    {entry.page && (
                      <a href={entry.page} target="_blank" rel="noreferrer">
                        {i18n.t("market:viewPage")}
                      </a>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <details className="market-sources">
        <summary>{i18n.t("market:sources.title")}</summary>
        <p className="muted">{i18n.t("market:sources.hint")}</p>
        <TextFieldList
          label={i18n.t("market:sources.label")}
          value={sources}
          placeholder={OFFICIAL_INDEX}
          onChange={(next) =>
            setConfig({ ...config, plugin_market: { sources: next } })
          }
        />
        <div className="actions">
          <button
            type="button"
            disabled={sources.length === 1 && sources[0] === OFFICIAL_INDEX}
            onClick={() =>
              setConfig({
                ...config,
                plugin_market: { sources: [OFFICIAL_INDEX] },
              })
            }
          >
            {i18n.t("market:sources.reset")}
          </button>
        </div>
      </details>
    </section>
  );
}
