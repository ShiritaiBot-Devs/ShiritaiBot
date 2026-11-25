import { LoadingBar } from "../components/LoadingBar";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Trash2 } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import { PageTabs } from "../components/PageTabs";
import { PluginMarket } from "../components/PluginMarket";
import { PluginSettingsForm } from "../components/PluginSettingsForm";
import { CONFIG_SNAPSHOT_KEY } from "../hooks/useConfigDraft";
import type { MarketPlugin, PluginInfo } from "../api/types";
import type { ConfigPageProps } from "./common";
import i18n, { tx } from "../i18n";

const statuses = {
  loaded: "plugins:status.loaded",
  disabled: "plugins:status.disabled",
  error: "plugins:status.error",
};

const platforms: Record<string, string> = { qq: "QQ", web: "WebUI" };

type PluginTab = "installed" | "market";
const TABS = [
  { id: "installed", label: "market:tabs.installed" },
  { id: "market", label: "market:tabs.market" },
] as const;

/** ``#/plugins/market`` opens the market tab, so it can be linked. */
function tabFromHash(): PluginTab {
  return /^#\/?plugins\/market\b/.test(window.location.hash)
    ? "market"
    : "installed";
}

function PluginCapabilities({ metadata }: { metadata: PluginInfo }) {
  const commands = metadata.commands ?? [];
  const tools = metadata.tools ?? [];
  const hooks = metadata.hooks ?? [];
  if (!commands.length && !tools.length && !hooks.length) return null;
  return (
    <div className="plugin-capabilities">
      {commands.length > 0 && (
        <details>
          <summary>
            {i18n.t("plugins:commands", { count: commands.length })}
          </summary>
          {commands.map((item) => (
            <div key={item.command} className="list-row">
              <code>{item.command}</code>
              <span className="muted">{item.description}</span>
              {item.owner_only && (
                <span className="tag">{i18n.t("plugins:ownerOnly")}</span>
              )}
            </div>
          ))}
        </details>
      )}
      {tools.length > 0 && (
        <details>
          <summary>{i18n.t("plugins:tools", { count: tools.length })}</summary>
          {tools.map((item) => (
            <div key={item.name} className="list-row">
              <code>{item.name}</code>
              <span className={"tag risk-" + item.risk}>{item.risk}</span>
            </div>
          ))}
        </details>
      )}
      {hooks.length > 0 && (
        <details>
          <summary>{i18n.t("plugins:hooks", { count: hooks.length })}</summary>
          {hooks.map((item) => (
            <div key={`${item.event}:${item.handler}`} className="list-row">
              <code>{item.event}</code>
              <span className="muted">{item.handler}</span>
              {item.priority !== 0 && (
                <span className="tag">
                  {i18n.t("plugins:priority", { priority: item.priority })}
                </span>
              )}
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

export function PluginsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const queryClient = useQueryClient();
  const discovery = useQuery({
    queryKey: ["plugins"],
    queryFn: () => client.plugins(),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [working, setWorking] = useState(false);
  const [id, setId] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [source, setSource] = useState("zip");
  const [tab, setTab] = useState<PluginTab>(tabFromHash);
  const latest = useRef(config);
  latest.current = config;
  const rows = discovery.data ?? [];
  const metadata = rows.find((item) => item.id === selected) ?? rows[0];
  const plugin =
    metadata &&
    (config.plugins.find((item) => item.id === metadata.id) ?? {
      id: metadata.id,
      enabled: metadata.enabled,
      settings: metadata.defaults ?? {},
    });

  function update(patch: Record<string, unknown>) {
    if (!plugin) return;
    const next = { ...plugin, ...patch };
    setConfig({
      ...config,
      plugins: config.plugins.some((item) => item.id === plugin.id)
        ? config.plugins.map((item) => (item.id === plugin.id ? next : item))
        : [...config.plugins, next],
    });
  }

  async function mutate(work: () => Promise<PluginInfo[]>, message: string) {
    setWorking(true);
    try {
      await act(async () => {
        const result = await work();
        queryClient.setQueryData(["plugins"], result);
        const snapshot = await client.configSnapshot();
        const saved = snapshot.settings;
        queryClient.setQueryData(CONFIG_SNAPSHOT_KEY, snapshot);
        queryClient.setQueryData(["config"], saved);
        const draft = latest.current;
        setConfig({
          ...draft,
          plugins: saved.plugins.map(
            (item) =>
              draft.plugins.find((current) => current.id === item.id) ?? item,
          ),
        });
        setInstalling(false);
        setRemoving(null);
      }, message);
    } finally {
      setWorking(false);
    }
  }

  function selectTab(next: PluginTab) {
    setTab(next);
    window.history.replaceState(
      null,
      "",
      next === "market" ? "#/plugins/market" : "#/plugins",
    );
  }

  async function installFromMarket(entry: MarketPlugin) {
    await mutate(
      () => client.installPluginGithub(entry.id, entry.repo, entry.commit),
      i18n.t("market:installed"),
    );
  }

  return (
    <>
      <PageTabs
        tabs={TABS}
        active={tab}
        onSelect={selectTab}
        prefix="plugins"
        label="market:tabs.label"
      />
      <div
        role="tabpanel"
        id="plugins-panel-market"
        aria-labelledby="plugins-tab-market"
        hidden={tab !== "market"}
      >
        {tab === "market" && (
          <PluginMarket
            client={client}
            config={config}
            setConfig={setConfig}
            installed={rows}
            working={working}
            act={act}
            onInstall={installFromMarket}
          />
        )}
      </div>
      <div
        role="tabpanel"
        id="plugins-panel-installed"
        aria-labelledby="plugins-tab-installed"
        hidden={tab !== "installed"}
      >
        {tab === "installed" && installedPanel()}
      </div>
    </>
  );

  function installedPanel() {
    return (
      <section className="panel">
        <div className="panel-head">
          <h2>{i18n.t("plugins:title")}</h2>
          <div className="actions">
            <button
              type="button"
              disabled={working}
              onClick={() =>
                void mutate(
                  () => client.reloadPlugins(),
                  i18n.t("plugins:reloaded"),
                )
              }
            >
              <RefreshCw size={14} />
              {i18n.t("plugins:reload")}
            </button>
            <button
              type="button"
              disabled={working}
              onClick={() => setInstalling(!installing)}
            >
              <Plus size={14} />
              {i18n.t("plugins:install.title")}
            </button>
          </div>
        </div>
        {installing && (
          <form
            className="config-editor"
            onSubmit={(event) => {
              event.preventDefault();
              void mutate(
                () =>
                  source === "github"
                    ? client.installPluginGithub(id, url)
                    : client.installPluginZip(id, file!),
                i18n.t("plugins:installed"),
              );
            }}
          >
            <p className="muted">{i18n.t("plugins:install.hint")}</p>
            <label className="field">
              {i18n.t("plugins:install.id")}
              <input
                aria-label={i18n.t("plugins:install.idLabel")}
                value={id}
                required
                pattern="[a-z0-9_]{1,64}"
                placeholder={i18n.t("plugins:install.idPlaceholder")}
                onChange={(event) => setId(event.target.value)}
              />
            </label>
            <label className="field">
              {i18n.t("plugins:install.source")}
              <select
                aria-label={i18n.t("plugins:install.source")}
                value={source}
                onChange={(event) => setSource(event.target.value)}
              >
                <option value="zip">{i18n.t("plugins:install.zip")}</option>
                <option value="github">
                  {i18n.t("plugins:install.github")}
                </option>
              </select>
            </label>
            {source === "github" ? (
              <label className="field">
                {i18n.t("plugins:install.repo")}
                <input
                  key="github"
                  aria-label={i18n.t("plugins:install.repo")}
                  type="url"
                  required
                  value={url}
                  placeholder="https://github.com/owner/repo"
                  onChange={(event) => setUrl(event.target.value)}
                />
              </label>
            ) : (
              <label className="field">
                {i18n.t("plugins:install.zipFile")}
                <input
                  key="zip"
                  aria-label={i18n.t("plugins:install.zipLabel")}
                  type="file"
                  accept=".zip"
                  onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                />
              </label>
            )}
            <div className="actions">
              <button
                type="submit"
                disabled={working || !id || (source === "zip" ? !file : !url)}
              >
                {i18n.t("plugins:install.submit")}
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => setInstalling(false)}
              >
                {i18n.t("plugins:install.cancel")}
              </button>
            </div>
          </form>
        )}
        {removing && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label={i18n.t("plugins:remove.dialog")}
            className="config-editor"
          >
            <h3>{i18n.t("plugins:remove.title", { name: removing })}</h3>
            <p>{i18n.t("plugins:remove.hint")}</p>
            <div className="actions">
              <button
                type="button"
                className="danger"
                disabled={working}
                onClick={() =>
                  void mutate(
                    () => client.removePlugin(removing),
                    i18n.t("plugins:removed"),
                  )
                }
              >
                {i18n.t("plugins:remove.confirm")}
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => setRemoving(null)}
              >
                {i18n.t("plugins:cancel")}
              </button>
            </div>
          </div>
        )}
        {discovery.isPending ? (
          <LoadingBar label={i18n.t("plugins:scanning")} />
        ) : discovery.isError ? (
          <p role="alert">{discovery.error.message}</p>
        ) : !rows.length ? (
          <EmptyState text={i18n.t("plugins:empty")} />
        ) : (
          <div className="config-workspace">
            <section
              className="config-sidebar"
              aria-label={i18n.t("plugins:list")}
            >
              {rows.map((item) => (
                <button
                  type="button"
                  key={item.id}
                  className={
                    "config-item" +
                    (item.id === metadata?.id ? " selected" : "")
                  }
                  onClick={() => setSelected(item.id)}
                >
                  <span
                    className={"dot " + (item.status === "loaded" ? "on" : "")}
                  />
                  <span title={item.short_description || undefined}>
                    <strong>{item.name || item.id}</strong>
                    <small>
                      {item.version
                        ? `v${item.version.replace(/^v/, "")} · `
                        : ""}
                      {tx(statuses[item.status])}
                      {item.short_description
                        ? ` · ${item.short_description}`
                        : ` · ${item.id}`}
                    </small>
                  </span>
                </button>
              ))}
            </section>
            <section className="config-editor">
              {plugin && metadata && (
                <>
                  <div className="panel-head">
                    <div>
                      <h2>
                        {metadata.name || plugin.id}
                        {metadata.version && (
                          <span className="tag">
                            v{metadata.version.replace(/^v/, "")}
                          </span>
                        )}
                        {metadata.api === 1 && (
                          <span
                            className="tag beta"
                            title={i18n.t("plugins:legacyHint")}
                          >
                            {i18n.t("plugins:legacy")}
                          </span>
                        )}
                      </h2>
                      <p className="muted">{metadata.description}</p>
                    </div>
                    <button
                      type="button"
                      className="danger"
                      disabled={working}
                      onClick={() => setRemoving(plugin.id)}
                    >
                      <Trash2 size={14} />
                      {i18n.t("plugins:remove.button")}
                    </button>
                  </div>
                  <p className="muted plugin-meta">
                    {i18n.t("plugins:meta.id", { id: plugin.id })} ·{" "}
                    {tx(statuses[metadata.status])}
                    {metadata.author && (
                      <>
                        {" "}
                        ·{" "}
                        {i18n.t("plugins:meta.author", {
                          author: metadata.author,
                        })}
                      </>
                    )}
                    {!!metadata.support_platforms?.length && (
                      <>
                        {" "}
                        ·{" "}
                        {i18n.t("plugins:meta.platforms", {
                          platforms: metadata.support_platforms
                            .map((item) => platforms[item] ?? item)
                            .join(i18n.t("common:enumSeparator")),
                        })}
                      </>
                    )}
                    {/^https?:\/\//i.test(metadata.repo ?? "") && (
                      <>
                        {" "}
                        ·{" "}
                        <a
                          href={metadata.repo}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {i18n.t("plugins:repo")}
                        </a>
                      </>
                    )}
                  </p>
                  <PluginCapabilities metadata={metadata} />
                  {metadata.error && (
                    <p role="alert">
                      {metadata.error}
                      {metadata.status === "error" && metadata.enabled
                        ? i18n.t("plugins:keepOld")
                        : ""}
                    </p>
                  )}
                  <Field
                    spec={{
                      key: "enabled",
                      label: i18n.t("plugins:enable.label"),
                      kind: "boolean",
                      help: i18n.t("plugins:enable.help"),
                    }}
                    value={plugin.enabled}
                    onChange={(enabled) => update({ enabled })}
                  />
                  <h3>{i18n.t("plugins:settings.title")}</h3>
                  <PluginSettingsForm
                    key={plugin.id}
                    plugin={plugin}
                    metadata={metadata}
                    onChange={(settings) => update({ settings })}
                  />
                  <div className="actions">
                    <button
                      type="button"
                      disabled={!Object.keys(metadata.defaults ?? {}).length}
                      onClick={() =>
                        update({ settings: { ...metadata.defaults } })
                      }
                    >
                      {i18n.t("plugins:settings.fillTemplate")}
                    </button>
                    <button
                      type="button"
                      disabled={working}
                      onClick={() =>
                        void mutate(
                          () => client.reloadPlugin(plugin.id),
                          i18n.t("plugins:reloadedOne"),
                        )
                      }
                    >
                      <RefreshCw size={14} />
                      {i18n.t("plugins:reloadOne")}
                    </button>
                  </div>
                  <p className="muted">{i18n.t("plugins:settings.hint")}</p>
                </>
              )}
            </section>
          </div>
        )}
      </section>
    );
  }
}
