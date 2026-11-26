import { PageTabs } from "../components/PageTabs";
import { ModelOptions } from "../components/ModelOptions";
import { DesktopSettings } from "../components/DesktopSettings";
import { ConfigTransfer } from "../components/ConfigTransfer";
import { useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Field, FieldForm } from "../components/FieldForm";
import { HelpMenuSections } from "../components/HelpMenuSections";
import { InstanceCollection } from "../components/InstanceCollection";
import {
  SessionLocaleList,
  UiLanguageSelect,
} from "../components/LanguageSettings";
import {
  BASIC_SETTINGS_FIELDS,
  PROACTIVE_FIELDS,
  IDENTITY_FIELDS,
  LANGUAGE_FIELDS,
  PERSONA_FIELDS,
  CONTEXT_FIELDS,
  EMOTION_FIELDS,
  GROUP_CONTEXT_FIELDS,
  SIMPLE_INTERJECT_FIELDS,
  INTERJECT_LIMIT_FIELD,
  HEARTFLOW_FIELDS,
  HEARTFLOW_ADVANCED_FIELDS,
  MEMORY_FIELDS,
  MESSAGE_MERGE_FIELDS,
  OUTBOUND_MESSAGE_FIELDS,
  WEB_SEARCH_FIELDS,
  WEB_SEARCH_PROVIDER_FIELDS,
  createPersona,
} from "../config/schema";
import type { ConfigPageProps } from "./common";
import i18n from "../i18n";

const SETTINGS_TABS = [
  { id: "general", label: "settings:tabs.general" },
  { id: "persona", label: "settings:tabs.persona" },
  { id: "extensions", label: "settings:tabs.extensions" },
] as const;

export function SettingsPage({
  client,
  act,
  config,
  setConfig,
  dirty,
  revision,
}: ConfigPageProps & { dirty: boolean; revision?: string }) {
  const { t } = useTranslation("settings");
  const group = config.group_context;
  const setHeartflow = (heartflow: typeof group.heartflow) =>
    setConfig({ ...config, group_context: { ...group, heartflow } });
  const [tab, setTab] =
    useState<(typeof SETTINGS_TABS)[number]["id"]>("general");
  return (
    <div className="settings-page">
      <PageTabs
        tabs={SETTINGS_TABS}
        active={tab}
        onSelect={setTab}
        prefix="settings"
        label={i18n.t("settings:tabsLabel")}
      />
      <div
        className="panel settings-card"
        role="tabpanel"
        id="settings-panel-general"
        aria-labelledby="settings-tab-general"
        hidden={tab !== "general"}
      >
        <DesktopSettings act={act} />
        <section className="settings-group">
          <h2>{t("language.title")}</h2>
          <div className="field-grid">
            {LANGUAGE_FIELDS.map((spec) => (
              <Field
                key={spec.key}
                spec={spec}
                value={config[spec.key as "locale"]}
                onChange={(next) => setConfig({ ...config, [spec.key]: next })}
              />
            ))}
            <UiLanguageSelect />
            <SessionLocaleList client={client} act={act} />
          </div>
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.identity")}</h2>
          <FieldForm
            value={config}
            fields={IDENTITY_FIELDS}
            onChange={setConfig}
          />
        </section>
        <details className="settings-group">
          <summary>{i18n.t("settings:tokens.title")}</summary>
          <ul className="muted">
            {(["admin", "adapter", "napcat"] as const).map((token) => (
              <li key={token}>
                <Trans
                  t={t}
                  i18nKey={`tokens.${token}`}
                  components={{ strong: <strong />, code: <code /> }}
                />
              </li>
            ))}
          </ul>
        </details>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.runtime")}</h2>
          <div className="field-grid">
            {BASIC_SETTINGS_FIELDS.filter((spec) => spec.key !== "persona").map(
              (spec) => (
                <Field
                  key={spec.key}
                  spec={spec}
                  value={config[spec.key as "persona" | "concurrency"]}
                  onChange={(next) =>
                    setConfig({ ...config, [spec.key]: next })
                  }
                />
              ),
            )}
          </div>
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.messageMerge")}</h2>
          <FieldForm
            value={config.policy}
            fields={MESSAGE_MERGE_FIELDS}
            onChange={(policy) => setConfig({ ...config, policy })}
          />
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.outbound")}</h2>
          <FieldForm
            value={config}
            fields={OUTBOUND_MESSAGE_FIELDS}
            onChange={setConfig}
          />
        </section>
        <HelpMenuSections
          client={client}
          value={config.help_menu_sections}
          onChange={(help_menu_sections) =>
            setConfig({ ...config, help_menu_sections })
          }
        />
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.context")}</h2>
          <div className="field-grid">
            <label className="field">
              <span className="field-name">
                {i18n.t("settings:context.strategy")}
              </span>
              <select
                aria-label={i18n.t("settings:context.strategy")}
                value={config.context.strategy}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    context: {
                      ...config.context,
                      strategy: event.target.value as "compress" | "discard",
                    },
                  })
                }
              >
                <option value="discard">
                  {i18n.t("settings:context.discard")}
                </option>
                <option value="compress">
                  {i18n.t("settings:context.compress")}
                </option>
              </select>
            </label>
            <label className="field">
              <span className="field-name">
                {i18n.t("settings:context.model")}
              </span>
              <select
                aria-label={i18n.t("settings:context.model")}
                value={config.context.compression_model}
                onChange={(event) =>
                  setConfig({
                    ...config,
                    context: {
                      ...config.context,
                      compression_model: event.target.value,
                    },
                  })
                }
              >
                <option value="">{i18n.t("settings:context.useLight")}</option>
                <ModelOptions
                  models={config.models.filter(
                    (item) =>
                      item.enabled &&
                      config.providers.some(
                        (provider) =>
                          provider.id === item.provider && provider.enabled,
                      ),
                  )}
                />
              </select>
            </label>
          </div>
          <FieldForm
            value={config.context}
            fields={CONTEXT_FIELDS}
            onChange={(context) => setConfig({ ...config, context })}
          />
          <Field
            spec={{
              key: "max_context_chars",
              label: i18n.t("settings:context.maxChars"),
              kind: "number",
              min: 4000,
              max: 1000000,
            }}
            value={config.policy.max_context_chars}
            onChange={(value) =>
              setConfig({
                ...config,
                policy: { ...config.policy, max_context_chars: Number(value) },
              })
            }
          />
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.groupContext")}</h2>
          <FieldForm
            value={config.group_context}
            fields={[
              ...GROUP_CONTEXT_FIELDS,
              ...(group.interject_mode === "simple"
                ? SIMPLE_INTERJECT_FIELDS
                : []),
              ...(group.interject_mode !== "off"
                ? [INTERJECT_LIMIT_FIELD]
                : []),
            ]}
            onChange={(group_context) =>
              setConfig({ ...config, group_context })
            }
          />
          {group.interject_mode === "heartflow" && (
            <>
              <FieldForm
                value={group.heartflow}
                fields={HEARTFLOW_FIELDS}
                onChange={setHeartflow}
              />
              <details>
                <summary>
                  {i18n.t("settings:sections.heartflowAdvanced")}
                </summary>
                <FieldForm
                  value={group.heartflow}
                  fields={HEARTFLOW_ADVANCED_FIELDS}
                  onChange={setHeartflow}
                />
              </details>
            </>
          )}
        </section>
        <ConfigTransfer
          client={client}
          act={act}
          dirty={dirty}
          revision={revision}
        />
      </div>
      <div
        className="panel settings-card"
        role="tabpanel"
        id="settings-panel-persona"
        aria-labelledby="settings-tab-persona"
        hidden={tab !== "persona"}
      >
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.basePersona")}</h2>
          <div className="field-grid">
            {BASIC_SETTINGS_FIELDS.filter((spec) => spec.key === "persona").map(
              (spec) => (
                <Field
                  key={spec.key}
                  spec={spec}
                  value={config[spec.key as "persona" | "concurrency"]}
                  onChange={(next) =>
                    setConfig({ ...config, [spec.key]: next })
                  }
                />
              ),
            )}
          </div>
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:persona.default")}</h2>
          <label className="field">
            <span className="field-name">
              {i18n.t("settings:persona.default")}
            </span>
            <small className="field-help">
              {i18n.t("settings:persona.defaultHint")}
            </small>
            <select
              aria-label={i18n.t("settings:persona.default")}
              value={config.default_persona}
              onChange={(event) =>
                setConfig({ ...config, default_persona: event.target.value })
              }
            >
              <option value="">
                {i18n.t("settings:sections.basePersona")}
              </option>
              {config.personas
                .filter((item) => item.enabled)
                .map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.id}
                  </option>
                ))}
            </select>
          </label>
        </section>
        <InstanceCollection
          title={i18n.t("settings:persona.collection")}
          items={config.personas}
          create={() => createPersona(config.personas)}
          onChange={(personas) => {
            const oldDefault = config.personas.find(
              (item) => item.id === config.default_persona,
            );
            const index = oldDefault ? config.personas.indexOf(oldDefault) : -1;
            let defaultPersona = config.default_persona;
            if (oldDefault && personas.length === config.personas.length) {
              defaultPersona = personas[index]?.enabled
                ? personas[index].id
                : "";
            } else if (
              !personas.some(
                (item) => item.id === defaultPersona && item.enabled,
              )
            ) {
              defaultPersona = "";
            }
            setConfig({ ...config, personas, default_persona: defaultPersona });
          }}
          fields={PERSONA_FIELDS}
          subtitle={(item) => item.name}
          emptyText={i18n.t("settings:persona.collectionHint")}
        />
      </div>
      <div
        className="panel settings-card"
        role="tabpanel"
        id="settings-panel-extensions"
        aria-labelledby="settings-tab-extensions"
        hidden={tab !== "extensions"}
      >
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.webSearch")}</h2>
          <FieldForm
            value={config.web_search}
            fields={WEB_SEARCH_FIELDS}
            onChange={(web_search) => setConfig({ ...config, web_search })}
          />
          <h3>
            {t("webSearch.providerSettings", {
              provider: {
                tavily: "Tavily",
                serpapi: "SerpAPI",
                deepseek: "DeepSeek",
              }[config.web_search.provider],
            })}
          </h3>
          <FieldForm
            key={config.web_search.provider}
            value={config.web_search[config.web_search.provider]}
            fields={WEB_SEARCH_PROVIDER_FIELDS[config.web_search.provider]}
            onChange={(provider) =>
              setConfig({
                ...config,
                web_search: {
                  ...config.web_search,
                  [config.web_search.provider]: provider,
                },
              })
            }
          />
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.memory")}</h2>
          <FieldForm
            value={config.memory}
            fields={MEMORY_FIELDS}
            onChange={(memory) => setConfig({ ...config, memory })}
          />
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.proactive")}</h2>
          <div className="field-grid">
            {PROACTIVE_FIELDS.map((spec) => (
              <Field
                key={spec.key}
                spec={spec}
                value={
                  config.proactive[spec.key as keyof typeof config.proactive]
                }
                onChange={(next) =>
                  setConfig({
                    ...config,
                    proactive: { ...config.proactive, [spec.key]: next },
                  })
                }
              />
            ))}
          </div>
        </section>
        <section className="settings-group">
          <h2>{i18n.t("settings:sections.emotion")}</h2>
          <FieldForm
            value={config.emotion}
            fields={EMOTION_FIELDS}
            onChange={(emotion) => setConfig({ ...config, emotion })}
          />
        </section>
      </div>
    </div>
  );
}
