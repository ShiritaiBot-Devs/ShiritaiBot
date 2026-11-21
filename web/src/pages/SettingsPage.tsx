import { PageTabs } from "../components/PageTabs";
import { ModelOptions } from "../components/ModelOptions";
import { useState } from "react";
import { Field, FieldForm } from "../components/FieldForm";
import { InstanceCollection } from "../components/InstanceCollection";
import {
  BASIC_SETTINGS_FIELDS,
  PROACTIVE_FIELDS,
  IDENTITY_FIELDS,
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
  WEB_SEARCH_FIELDS,
  WEB_SEARCH_PROVIDER_FIELDS,
  createPersona,
} from "../config/schema";
import type { ConfigPageProps } from "./common";

const SETTINGS_TABS = [
  { id: "general", label: "通用设定" },
  { id: "persona", label: "人格设定" },
  { id: "extensions", label: "扩展功能" },
] as const;

export function SettingsPage({ config, setConfig }: ConfigPageProps) {
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
        label="配置分类"
      />
      <div
        className="panel settings-card"
        role="tabpanel"
        id="settings-panel-general"
        aria-labelledby="settings-tab-general"
        hidden={tab !== "general"}
      >
        <section className="settings-group">
          <h2>称呼与唤起</h2>
          <FieldForm
            value={config}
            fields={IDENTITY_FIELDS}
            onChange={setConfig}
          />
        </section>
        <details className="settings-group">
          <summary>令牌使用说明</summary>
          <ul className="muted">
            <li>
              <strong>管理令牌</strong>：登录这个 WebUI 用。存在
              data/web.token， 也可以用命令 <code>shiritaibot token</code>{" "}
              查看。未登录时打开首页就是登录页 （也可以直接访问 /login）。
            </li>
            <li>
              <strong>适配器访问令牌</strong>：Bot 连接 OneBot
              协议端时带的密码，在「适配器」页填写； 要和 NapCat
              里配置的访问令牌一致。没设访问令牌就留空。
            </li>
            <li>
              <strong>NapCat WebUI 令牌</strong>：只用于打开 NapCat
              自己的管理页面 （默认 http://127.0.0.1:6099 ），和上面两个无关。
            </li>
          </ul>
        </details>
        <section className="settings-group">
          <h2>运行设置</h2>
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
          <h2>消息合并</h2>
          <FieldForm
            value={config.policy}
            fields={MESSAGE_MERGE_FIELDS}
            onChange={(policy) => setConfig({ ...config, policy })}
          />
        </section>
        <section className="settings-group">
          <h2>上下文管理</h2>
          <div className="field-grid">
            <label className="field">
              <span className="field-name">上下文管理策略</span>
              <select
                aria-label="上下文管理策略"
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
                <option value="discard">超限丢弃旧轮次</option>
                <option value="compress">压缩旧轮次为摘要</option>
              </select>
            </label>
            <label className="field">
              <span className="field-name">上下文压缩模型</span>
              <select
                aria-label="上下文压缩模型"
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
                <option value="">使用 light 路由</option>
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
              label: "上下文字符上限",
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
          <h2>群聊上下文感知</h2>
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
                <summary>心流高级设置</summary>
                <FieldForm
                  value={group.heartflow}
                  fields={HEARTFLOW_ADVANCED_FIELDS}
                  onChange={setHeartflow}
                />
              </details>
            </>
          )}
        </section>
      </div>
      <div
        className="panel settings-card"
        role="tabpanel"
        id="settings-panel-persona"
        aria-labelledby="settings-tab-persona"
        hidden={tab !== "persona"}
      >
        <section className="settings-group">
          <h2>基础人设</h2>
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
          <h2>默认人格</h2>
          <label className="field">
            <span className="field-name">默认人格</span>
            <small className="field-help">
              使用 /personas 查看名称和标识，/persona 标识
              为当前会话切换人格；/persona default
              恢复默认。仅主人可切换，下一轮生效。
            </small>
            <select
              aria-label="默认人格"
              value={config.default_persona}
              onChange={(event) =>
                setConfig({ ...config, default_persona: event.target.value })
              }
            >
              <option value="">基础人设</option>
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
          title="人格设定"
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
          emptyText="添加多个人格，通过斜杠命令切换。"
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
          <h2>网页搜索</h2>
          <FieldForm
            value={config.web_search}
            fields={WEB_SEARCH_FIELDS}
            onChange={(web_search) => setConfig({ ...config, web_search })}
          />
          <h3>
            {
              { tavily: "Tavily", serpapi: "SerpAPI", deepseek: "DeepSeek" }[
                config.web_search.provider
              ]
            }{" "}
            配置
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
          <h2>长期记忆</h2>
          <FieldForm
            value={config.memory}
            fields={MEMORY_FIELDS}
            onChange={(memory) => setConfig({ ...config, memory })}
          />
        </section>
        <section className="settings-group">
          <h2>主动性与打扰策略</h2>
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
          <h2>情绪（开发者选项）</h2>
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
