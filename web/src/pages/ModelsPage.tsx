import { LoadingBar } from "../components/LoadingBar";
import { useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BadgeDollarSign,
  Check,
  Download,
  Plus,
  Search,
  Trash2,
} from "lucide-react";
import type {
  DiscoveredModel,
  ModelConfig,
  Protocol,
  ProviderConfig,
} from "../api/types";
import { Field, FieldForm, jsonObjectError } from "../components/FieldForm";
import { ModelTestButton } from "../components/ModelTestButton";
import { ModelOptions } from "../components/ModelOptions";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { SessionPicker } from "../components/SessionPicker";
import { configuredModelName, modelName, modelNameById } from "../lib/models";
import { isMainSession } from "../lib/sessions";
import { EmptyState } from "../components/EmptyState";
import { ProviderPresetMenu } from "../components/ProviderPresetMenu";
import { SubscriptionPanel } from "../components/SubscriptionPanel";
import {
  EMBEDDING_MODEL_FIELDS,
  MODEL_FIELDS,
  PROVIDER_FIELDS,
  VISION_FIELDS,
  createProvider,
} from "../config/schema";
import { uniqueId } from "../lib/utils";
import type { ConfigPageProps } from "./common";
import i18n, { tx } from "../i18n";

type Category = "text" | "decision" | "embedding";

const ROUTES: { key: string; label: string; help: string }[] = [
  {
    key: "chat",
    label: "models:routes.chat.label",
    help: "models:routes.chat.help",
  },
  {
    key: "agent",
    label: "models:routes.agent.label",
    help: "models:routes.agent.help",
  },
  {
    key: "agent_chat",
    label: "models:routes.agent_chat.label",
    help: "models:routes.agent_chat.help",
  },
  {
    key: "router",
    label: "models:routes.router.label",
    help: "models:routes.router.help",
  },
  {
    key: "light",
    label: "models:routes.light.label",
    help: "models:routes.light.help",
  },
  {
    key: "memory",
    label: "models:routes.memory.label",
    help: "models:routes.memory.help",
  },
  {
    key: "approval",
    label: "models:routes.approval.label",
    help: "models:routes.approval.help",
  },
  {
    key: "vision",
    label: "models:routes.vision.label",
    help: "models:routes.vision.help",
  },
  {
    key: "meme",
    label: "models:routes.meme.label",
    help: "models:routes.meme.help",
  },
  {
    key: "embedding",
    label: "models:routes.embedding.label",
    help: "models:routes.embedding.help",
  },
];

// Decision models (systemone) classify only, so they fit just these routes.
const DECISION_ROUTES = ["router", "light", "memory", "approval", "meme"];
const GLOBAL_ONLY_ROUTES = ["embedding", "meme"];
const EMBEDDING_PROTOCOLS: Protocol[] = [
  "chat_completions",
  "responses",
  "gemini",
];
const TEXT_PROTOCOLS: Protocol[] = [
  "chat_completions",
  "responses",
  "openai_subscription",
  "anthropic",
  "gemini",
];
const CATEGORIES: {
  key: Category;
  label: string;
  routes: string[];
  protocols: Protocol[];
}[] = [
  {
    key: "text",
    label: "models:categories.text",
    routes: [
      "chat",
      "agent",
      "agent_chat",
      "router",
      "light",
      "memory",
      "approval",
      "vision",
      "meme",
    ],
    protocols: TEXT_PROTOCOLS,
  },
  {
    key: "decision",
    label: "models:categories.decision",
    routes: DECISION_ROUTES,
    protocols: ["systemone"],
  },
  {
    key: "embedding",
    label: "Embedding",
    routes: ["embedding"],
    protocols: EMBEDDING_PROTOCOLS,
  },
];
const INTRO: Record<Category, string> = {
  text: "models:intro.text",
  decision: "models:intro.decision",
  embedding: "models:intro.embedding",
};

function modelFits(model: ModelConfig, category: Category) {
  return (model.kind === "embedding") === (category === "embedding");
}

export function ModelsPage({
  client,
  config,
  setConfig,
  act,
  initialSession = "web:owner",
}: ConfigPageProps & { initialSession?: string }) {
  const [category, setCategory] = useState<Category>("text");
  const [selected, setSelected] = useState(0);
  const [tab, setTab] = useState<"provider" | "routes">("provider");
  const [scope, setScope] = useState("global");
  const [session, setSession] = useState(
    isMainSession(initialSession) ? initialSession : "web:owner",
  );
  const [remote, setRemote] = useState<{
    source: string;
    rows: DiscoveredModel[];
  } | null>(null);
  const [fetching, setFetching] = useState(false);
  const [pricing, setPricing] = useState<number | null>(null);
  // Async price lookups patch the latest draft, not the one captured at click time.
  const latest = useRef(config);
  latest.current = config;
  const [query, setQuery] = useState("");

  const current = CATEGORIES.find((item) => item.key === category)!;
  const visible = config.providers
    .map((item, position) => ({ item, position }))
    .filter(({ item }) => current.protocols.includes(item.protocol));
  const index =
    visible.find(({ position }) => position === selected)?.position ??
    visible[0]?.position ??
    -1;
  const provider = index >= 0 ? config.providers[index] : undefined;
  const subscription = provider?.protocol === "openai_subscription";
  const status = useQuery({
    queryKey: ["memory-status"],
    queryFn: () => client.memoryStatus(),
    enabled: category === "embedding",
    refetchInterval: 10000,
  });
  const perSession = scope === "session" && category !== "embedding";
  const visibleRoutes = perSession
    ? current.routes.filter((key) => !GLOBAL_ONLY_ROUTES.includes(key))
    : current.routes;
  const sessionRoutes = config.session_routes ?? {};
  const overrides = sessionRoutes[session] ?? {};
  const chats = useQuery({
    queryKey: ["web-chats"],
    queryFn: () => client.webChats(),
    enabled: tab === "routes" && perSession,
  });

  function routeAccepts(key: string, model: ModelConfig) {
    if (model.kind === "embedding") return key === "embedding";
    if (key === "embedding") return false;
    const protocol = config.providers.find(
      (item) => item.id === model.provider,
    )?.protocol;
    if (protocol === "systemone") return DECISION_ROUTES.includes(key);
    if (
      (key === "vision" || key === "meme") &&
      !model.modalities.includes("image")
    )
      return false;
    return true;
  }
  const source = provider ? JSON.stringify(provider) : "";
  const results = remote?.source === source ? remote.rows : null;

  function updateProvider(patch: Partial<ProviderConfig>) {
    if (!provider) return;
    const next = { ...provider, ...patch };
    setConfig({
      ...config,
      providers: config.providers.map((item, position) =>
        position === index ? next : item,
      ),
      models: config.models.map((model) =>
        patch.id !== undefined && model.provider === provider.id
          ? { ...model, provider: patch.id }
          : model,
      ),
    });
  }

  function mapRouteModels(mapModels: (models: string[]) => string[]) {
    const mapRoutes = (routes: Record<string, string[]>) =>
      Object.entries(routes).map(
        ([route, models]) => [route, mapModels(models)] as const,
      );
    const sessions: typeof sessionRoutes = {};
    for (const [id, routes] of Object.entries(sessionRoutes)) {
      const kept = mapRoutes(routes).filter(([, models]) => models.length);
      if (kept.length) sessions[id] = Object.fromEntries(kept);
    }
    return {
      routes: Object.fromEntries(mapRoutes(config.routes)),
      session_routes: sessions,
    };
  }

  function updateSessionRoutes(next: Record<string, string[]>) {
    const sessions = { ...sessionRoutes };
    if (Object.keys(next).length) sessions[session] = next;
    else delete sessions[session];
    setConfig({ ...config, session_routes: sessions });
  }

  function updateRoute(key: string, route: string[]) {
    if (!perSession) {
      setConfig({ ...config, routes: { ...config.routes, [key]: route } });
      return;
    }
    const next = { ...overrides };
    if (route.length) next[key] = route;
    else delete next[key];
    updateSessionRoutes(next);
  }

  function addModel(row?: DiscoveredModel) {
    if (!provider) return;
    const id = uniqueId(row?.id || "model-new", config.models);
    const model: ModelConfig = {
      id,
      provider: provider.id,
      model: row?.id || "",
      enabled: true,
      max_output: Math.min(128000, Math.max(64, row?.max_output || 4096)),
      max_context: 0,
      modalities: ["text"],
      extra_body: {},
      kind: category === "embedding" ? "embedding" : "text",
      dims: 0,
      currency: "USD",
      price_input: 0,
      price_cached: 0,
      price_output: 0,
      ...(row?.pricing ?? {}),
    };
    const routes = { ...config.routes };
    const defaults = {
      text: ["agent", "chat", "light"],
      decision: ["light"],
      embedding: ["embedding"],
    }[category];
    for (const key of defaults) {
      if (!routes[key]?.length) routes[key] = [id];
    }
    setConfig({ ...config, models: [...config.models, model], routes });
  }

  function updateModel(position: number, patch: Record<string, unknown>) {
    const old = config.models[position];
    const routeChanges =
      patch.id !== undefined
        ? mapRouteModels((ids) =>
            ids.map((id) => (id === old.id ? String(patch.id) : id)),
          )
        : {};
    setConfig({
      ...config,
      ...routeChanges,
      models: config.models.map((model, itemIndex) =>
        itemIndex === position ? { ...model, ...patch } : model,
      ),
    });
  }

  async function fetchPricing(model: ModelConfig, target: ProviderConfig) {
    let rows = results;
    if (!rows) {
      rows = await client.discoverModels(target);
      setRemote({ source: JSON.stringify(target), rows });
    }
    const found = rows.find((row) => row.id === model.model);
    if (!found)
      throw new Error(
        i18n.t("models:pricing.notListed", {
          model: configuredModelName(model),
        }),
      );
    const price = found.pricing;
    if (!price) throw new Error(i18n.t("models:pricing.notPublished"));
    const current = latest.current;
    setConfig({
      ...current,
      models: current.models.map((item) =>
        item.id === model.id ? { ...item, ...price } : item,
      ),
    });
    return i18n.t("models:pricing.summary", {
      input: price.price_input,
      cached: price.price_cached,
      output: price.price_output,
      currency: price.currency,
    });
  }

  return (
    <>
      <div
        className="range-tabs model-tabs"
        role="group"
        aria-label={i18n.t("models:categoryGroup")}
      >
        {CATEGORIES.map(({ key, label }) => (
          <button
            type="button"
            key={key}
            className={category === key ? "selected" : ""}
            aria-pressed={category === key}
            onClick={() => {
              setCategory(key);
              setRemote(null);
              setQuery("");
            }}
          >
            {tx(label)}
          </button>
        ))}
      </div>
      <p className="muted model-intro">{tx(INTRO[category])}</p>
      <div className="config-workspace">
        <section
          className="config-sidebar"
          aria-label={i18n.t("models:providerList")}
        >
          <div className="panel-head">
            <h2>{i18n.t("models:providers")}</h2>
            <ProviderPresetMenu
              onSelect={(preset) => {
                const created = createProvider(config.providers, preset);
                setConfig({
                  ...config,
                  providers: [...config.providers, created],
                });
                if (!current.protocols.includes(preset.protocol))
                  setCategory(
                    preset.protocol === "systemone" ? "decision" : "text",
                  );
                setSelected(config.providers.length);
                setTab("provider");
                setRemote(null);
                setQuery("");
              }}
            />
          </div>
          {visible.map(({ item, position }) => (
            <button
              type="button"
              key={position}
              className={
                "config-item" +
                (index === position && tab === "provider" ? " selected" : "")
              }
              onClick={() => {
                setSelected(position);
                setTab("provider");
                setQuery("");
              }}
            >
              <span className={"dot " + (item.enabled ? "on" : "")} />
              <span>
                <strong>{item.id || i18n.t("models:unnamed")}</strong>
                <small>{item.base_url}</small>
              </span>
            </button>
          ))}
          {visible.length === 0 && (
            <p className="muted sidebar-hint">
              {category === "decision"
                ? i18n.t("models:emptyDecision")
                : i18n.t("models:emptyText")}
            </p>
          )}
          <button
            type="button"
            className={
              "config-item route-item" + (tab === "routes" ? " selected" : "")
            }
            onClick={() => setTab("routes")}
          >
            <span>
              <strong>{i18n.t("models:routeList.title")}</strong>
              <small>{i18n.t("models:routeList.subtitle")}</small>
            </span>
          </button>
        </section>
        <section className="config-editor">
          {tab === "routes" ? (
            <>
              <h2>{i18n.t("models:routeList.title")}</h2>
              <p className="muted">
                {i18n.t("models:routeList.intro")}
                {category === "text" &&
                  !perSession &&
                  i18n.t("models:routeList.firstModel")}
              </p>
              {category !== "embedding" && (
                <div className="field-grid">
                  <label className="field">
                    <span className="field-name">{i18n.t("models:scope")}</span>
                    <select
                      value={scope}
                      onChange={(event) => setScope(event.target.value)}
                    >
                      <option value="global">
                        {i18n.t("models:scopeGlobal")}
                      </option>
                      <option value="session">
                        {i18n.t("models:scopeSession")}
                      </option>
                    </select>
                  </label>
                  {perSession && (
                    <SessionPicker
                      client={client}
                      adapters={config.adapters}
                      suggestions={[
                        ...Object.keys(config.policy.sessions),
                        ...Object.keys(sessionRoutes),
                      ]}
                      session={session}
                      onChange={setSession}
                      onReset={
                        Object.keys(overrides).length
                          ? () => updateSessionRoutes({})
                          : undefined
                      }
                      titles={
                        new Map(
                          chats.data?.map((chat) => [chat.id, chat.title]),
                        )
                      }
                    />
                  )}
                </div>
              )}
              {perSession && (
                <p className="muted">{i18n.t("models:sessionHint")}</p>
              )}
              {perSession && chats.error && (
                <QueryErrorNotice
                  message={i18n.t("models:chatsFailed", {
                    error: chats.error.message,
                  })}
                />
              )}
              {category === "embedding" && (
                <p className="muted" aria-label={i18n.t("models:vectorStatus")}>
                  {status.data ? (
                    status.data.model ? (
                      i18n.t("models:vectors.indexed", {
                        model: status.data.model.replace(":", "/"),
                        indexed: status.data.indexed,
                        pending: status.data.pending,
                      })
                    ) : (
                      i18n.t("models:vectors.none", {
                        count: status.data.pending,
                      })
                    )
                  ) : (
                    <LoadingBar
                      compact
                      label={i18n.t("models:vectorsLoading")}
                    />
                  )}
                </p>
              )}
              {category === "text" && !perSession && (
                <div className="field field-toggle route-split">
                  <div>
                    <span className="field-name">
                      {i18n.t("models:split.label")}
                    </span>
                    <small className="field-help">
                      {i18n.t("models:split.help")}
                    </small>
                  </div>
                  <input
                    aria-label={i18n.t("models:split.label")}
                    type="checkbox"
                    checked={Boolean(config.agent_split)}
                    onChange={(event) =>
                      setConfig({
                        ...config,
                        agent_split: event.target.checked,
                      })
                    }
                  />
                </div>
              )}
              {category === "text" && !perSession && (
                <div className="field field-toggle route-split">
                  <div>
                    <span className="field-name">
                      {i18n.t("models:handoff.label")}
                    </span>
                    <small className="field-help">
                      {i18n.t("models:handoff.help")}
                    </small>
                  </div>
                  <input
                    aria-label={i18n.t("models:handoff.label")}
                    type="checkbox"
                    disabled={!config.agent_split}
                    checked={Boolean(config.agent_chat_handoff)}
                    onChange={(event) =>
                      setConfig({
                        ...config,
                        agent_chat_handoff: event.target.checked,
                      })
                    }
                  />
                </div>
              )}
              {ROUTES.filter(({ key }) => visibleRoutes.includes(key)).map(
                ({ key, label, help }) => {
                  const globalRoute = config.routes[key] ?? [];
                  const route = perSession
                    ? (overrides[key] ?? [])
                    : globalRoute;
                  const usesLight =
                    !globalRoute.length &&
                    (key === "memory" || key === "approval");
                  const inherited = usesLight
                    ? (overrides.light ?? config.routes.light ?? [])
                    : globalRoute;
                  const inactive =
                    !config.agent_split &&
                    (key === "agent_chat" || key === "router");
                  return (
                    <div
                      className={"route-block" + (inactive ? " inactive" : "")}
                      key={key}
                    >
                      <h3>{tx(label)}</h3>
                      {help && <p className="muted route-help">{tx(help)}</p>}
                      {perSession && !route.length && (
                        <p className="muted route-help">
                          {usesLight
                            ? i18n.t("models:inherit.light")
                            : i18n.t("models:inherit.global")}{" "}
                          ·{" "}
                          {inherited
                            .map((id) => modelNameById(config.models, id))
                            .join(" → ") || i18n.t("models:notConfigured")}
                        </p>
                      )}
                      {route.map((id, position) => (
                        <div className="inline" key={id + position}>
                          <span>
                            {position + 1}. {modelNameById(config.models, id)}
                          </span>
                          <button
                            type="button"
                            aria-label={i18n.t("models:moveUpLabel", {
                              model: modelNameById(config.models, id),
                            })}
                            disabled={position === 0}
                            onClick={() => {
                              const next = [...route];
                              [next[position - 1], next[position]] = [
                                next[position],
                                next[position - 1],
                              ];
                              updateRoute(key, next);
                            }}
                          >
                            {i18n.t("models:moveUp")}
                          </button>
                          <button
                            type="button"
                            aria-label={i18n.t("models:removeFromRoute", {
                              model: modelNameById(config.models, id),
                            })}
                            onClick={() =>
                              updateRoute(
                                key,
                                route.filter((_, item) => item !== position),
                              )
                            }
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      ))}
                      <select
                        aria-label={i18n.t("models:addToRoute", { route: key })}
                        value=""
                        onChange={(event) => {
                          if (!event.target.value) return;
                          updateRoute(key, [...route, event.target.value]);
                        }}
                      >
                        <option value="">
                          ＋{" "}
                          {perSession && !route.length
                            ? i18n.t("models:addSessionModel")
                            : i18n.t("models:addModel")}
                        </option>
                        <ModelOptions
                          models={config.models.filter(
                            (model) =>
                              !route.includes(model.id) &&
                              routeAccepts(key, model),
                          )}
                        />
                      </select>
                      {key === "vision" && !perSession && (
                        <FieldForm
                          value={config.vision}
                          fields={VISION_FIELDS}
                          onChange={(vision) =>
                            setConfig({ ...config, vision })
                          }
                        />
                      )}
                    </div>
                  );
                },
              )}
            </>
          ) : provider ? (
            <>
              <div className="panel-head">
                <div>
                  <h2>{provider.id || i18n.t("models:unnamedProvider")}</h2>
                  <p className="muted">
                    {subscription ? "OpenAI Subscription" : provider.protocol}
                  </p>
                </div>
                <button
                  type="button"
                  className="danger"
                  aria-label={i18n.t("models:removeProvider")}
                  onClick={() => {
                    const ids = config.models
                      .filter((model) => model.provider === provider.id)
                      .map((model) => model.id);
                    setConfig({
                      ...config,
                      providers: config.providers.filter(
                        (_, position) => position !== index,
                      ),
                      models: config.models.filter(
                        (model) => !ids.includes(model.id),
                      ),
                      ...mapRouteModels((refs) =>
                        refs.filter((id) => !ids.includes(id)),
                      ),
                    });
                  }}
                >
                  <Trash2 size={16} />
                  {i18n.t("models:remove")}
                </button>
              </div>
              {subscription && (
                <SubscriptionPanel
                  key={provider.id}
                  id={provider.id}
                  client={client}
                  act={act}
                  onConnectionChange={() => setRemote(null)}
                />
              )}
              <div className="field-grid">
                {PROVIDER_FIELDS.filter(
                  (spec) =>
                    !subscription ||
                    !["id", "protocol", "base_url", "api_key"].includes(
                      spec.key,
                    ),
                )
                  .map((spec) =>
                    spec.key === "protocol"
                      ? {
                          ...spec,
                          options: current.protocols.filter(
                            (protocol) => protocol !== "openai_subscription",
                          ),
                        }
                      : spec,
                  )
                  .map((spec) => (
                    <Field
                      key={spec.key}
                      spec={spec}
                      value={provider[spec.key as keyof ProviderConfig]}
                      onChange={(next) =>
                        updateProvider({
                          [spec.key]: next,
                        } as Partial<ProviderConfig>)
                      }
                    />
                  ))}
              </div>
              <section className="model-section">
                <div className="panel-head">
                  <h3>{i18n.t("models:modelsTitle")}</h3>
                  <div className="inline">
                    <button
                      type="button"
                      disabled={fetching}
                      onClick={() => {
                        setFetching(true);
                        void act(async () => {
                          try {
                            const rows = await client.discoverModels(provider);
                            setRemote({ source, rows });
                            return i18n.t("models:modelCount", {
                              count: rows.length,
                            });
                          } finally {
                            setFetching(false);
                          }
                        }, i18n.t("models:fetched"));
                      }}
                    >
                      <Download size={15} />
                      {fetching ? (
                        <LoadingBar compact label={i18n.t("models:fetching")} />
                      ) : (
                        i18n.t("models:fetchModels")
                      )}
                    </button>
                    <button type="button" onClick={() => addModel()}>
                      <Plus size={15} />
                      {i18n.t("models:addManually")}
                    </button>
                  </div>
                </div>
                <p className="muted">
                  {subscription
                    ? i18n.t("models:subscriptionHint")
                    : i18n.t("models:fetchHint")}
                </p>
                {results && (
                  <div className="remote-models">
                    <label className="inline">
                      <Search size={16} aria-hidden="true" />
                      <input
                        aria-label={i18n.t("models:searchRemote")}
                        placeholder={i18n.t("models:searchPlaceholder")}
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                      />
                    </label>
                    <div className="remote-list">
                      {results
                        .filter((row) =>
                          (modelName(provider.id, row.id) + " " + row.name)
                            .toLowerCase()
                            .includes(query.toLowerCase()),
                        )
                        .map((row) => {
                          const added = config.models.some(
                            (model) =>
                              model.provider === provider.id &&
                              model.model === row.id,
                          );
                          return (
                            <div className="list-row" key={row.id}>
                              <div>
                                <strong>
                                  {modelName(provider.id, row.id)}
                                </strong>
                                <small>
                                  {row.name !== row.id ? row.name : ""}
                                  {row.pricing &&
                                    i18n.t("models:remotePrice", {
                                      sep: row.name !== row.id ? " · " : "",
                                      input: row.pricing.price_input,
                                      output: row.pricing.price_output,
                                    })}
                                </small>
                              </div>
                              <button
                                type="button"
                                disabled={added}
                                aria-label={i18n.t("models:addModelLabel", {
                                  model: modelName(provider.id, row.id),
                                })}
                                onClick={() => addModel(row)}
                              >
                                {added ? (
                                  <Check size={16} />
                                ) : (
                                  <Plus size={16} />
                                )}
                              </button>
                            </div>
                          );
                        })}
                      {results.length === 0 && (
                        <p className="muted">{i18n.t("models:noRemote")}</p>
                      )}
                    </div>
                  </div>
                )}
                {config.models.map((model, position) =>
                  model.provider === provider.id &&
                  modelFits(model, category) ? (
                    <div className="model-card" key={position}>
                      <div className="panel-head">
                        <h3>{configuredModelName(model)}</h3>
                        <button
                          type="button"
                          aria-label={i18n.t("models:deleteModelLabel", {
                            model: configuredModelName(model),
                          })}
                          onClick={() =>
                            setConfig({
                              ...config,
                              models: config.models.filter(
                                (_, itemIndex) => itemIndex !== position,
                              ),
                              ...mapRouteModels((ids) =>
                                ids.filter((id) => id !== model.id),
                              ),
                            })
                          }
                        >
                          <Trash2 size={15} />
                        </button>
                      </div>
                      <div className="field-grid">
                        {(category === "embedding"
                          ? EMBEDDING_MODEL_FIELDS
                          : MODEL_FIELDS
                        )
                          .filter(
                            (spec) =>
                              !subscription ||
                              ![
                                "currency",
                                "price_input",
                                "price_cached",
                                "price_output",
                              ].includes(spec.key),
                          )
                          .map((spec) => (
                            <Field
                              key={spec.key}
                              spec={
                                subscription && spec.key === "max_output"
                                  ? {
                                      ...spec,
                                      label: i18n.t(
                                        "models:subscriptionOutput.label",
                                      ),
                                      help: i18n.t(
                                        "models:subscriptionOutput.help",
                                      ),
                                    }
                                  : subscription && spec.key === "extra_body"
                                    ? {
                                        ...spec,
                                        placeholder:
                                          '{"reasoning":{"effort":"medium"}}',
                                        help: i18n.t(
                                          "models:subscriptionExtra.help",
                                        ),
                                      }
                                    : spec
                              }
                              value={model[spec.key as keyof typeof model]}
                              onChange={(next) =>
                                updateModel(position, { [spec.key]: next })
                              }
                            />
                          ))}
                      </div>
                      <div className="inline">
                        <ModelTestButton
                          test={() => client.testModel(provider, model)}
                          act={act}
                          disabled={Boolean(jsonObjectError(model.extra_body))}
                        />
                        {!subscription && (
                          <button
                            type="button"
                            disabled={pricing !== null || !model.model}
                            aria-busy={pricing === position}
                            onClick={() => {
                              setPricing(position);
                              void act(async () => {
                                try {
                                  return await fetchPricing(model, provider);
                                } finally {
                                  setPricing(null);
                                }
                              }, i18n.t("models:pricingFetched"));
                            }}
                          >
                            <BadgeDollarSign size={15} aria-hidden="true" />
                            {pricing === position ? (
                              <LoadingBar
                                compact
                                label={i18n.t("models:fetching")}
                              />
                            ) : (
                              i18n.t("models:fetchPricing")
                            )}
                          </button>
                        )}
                      </div>
                    </div>
                  ) : null,
                )}
              </section>
            </>
          ) : (
            <EmptyState text={i18n.t("models:emptyProviders")} />
          )}
        </section>
      </div>
    </>
  );
}
