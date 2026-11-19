import { useState } from "react";
import { isMainSession, sessionTitle } from "../lib/sessions";
import type { SessionConfigPageProps } from "../pages/common";
import { SessionPicker } from "./SessionPicker";

const DEFAULT_NAME = "默认知识库";

export function KnowledgeLibraries({
  client,
  config,
  setConfig,
  currentSession,
  draftSessions,
}: SessionConfigPageProps) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [session, setSession] = useState(
    isMainSession(currentSession) ? currentSession : "web:owner",
  );
  const knowledge = config.knowledge;
  const libraries = knowledge.libraries ?? {};
  const bindings = knowledge.sessions ?? {};
  const validId =
    /^[a-z][a-z0-9_-]{0,63}$/.test(id) &&
    id !== "default" &&
    id !== "disabled" &&
    !Object.hasOwn(libraries, id);

  function update(next: Partial<typeof knowledge>) {
    setConfig({ ...config, knowledge: { ...knowledge, ...next } });
  }

  function bind(library: string) {
    const sessions = { ...bindings };
    if (library) sessions[session] = library;
    else delete sessions[session];
    update({ sessions });
  }

  function label(library: string) {
    if (library === "disabled") return "停用知识库";
    if (library === "default") return DEFAULT_NAME;
    return libraries[library]?.name ?? library;
  }

  return (
    <section className="panel">
      <h2>知识库设置</h2>
      <form
        className="inline"
        onSubmit={(event) => {
          event.preventDefault();
          if (!validId || !name.trim()) return;
          update({ libraries: { ...libraries, [id]: { name: name.trim() } } });
          setId("");
          setName("");
        }}
      >
        <input
          aria-label="知识库标识"
          placeholder="标识，例如 project"
          maxLength={64}
          value={id}
          onChange={(event) => setId(event.target.value)}
          required
        />
        <input
          aria-label="知识库名称"
          placeholder="显示名称"
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <button type="submit" disabled={!validId || !name.trim()}>
          添加知识库
        </button>
      </form>
      {id && !validId && (
        <p className="muted">
          标识须以小写字母开头，使用字母、数字、下划线或连字符，且未被占用。
        </p>
      )}
      {Object.entries(libraries).map(([key, library]) => (
        <div className="inline" key={key}>
          <code>{key}</code>
          <input
            aria-label={`${key} 的显示名称`}
            value={library.name}
            maxLength={80}
            onChange={(event) =>
              update({
                libraries: {
                  ...libraries,
                  [key]: { name: event.target.value },
                },
              })
            }
          />
          <button
            type="button"
            disabled={Object.values(bindings).includes(key)}
            title="移出配置会保留磁盘上的文档"
            onClick={() => {
              const next = { ...libraries };
              delete next[key];
              update({ libraries: next });
            }}
          >
            移出配置
          </button>
        </div>
      ))}
      <h3>会话绑定</h3>
      <p className="muted">
        同一个库可以绑定多个会话。绑定到群聊后，群成员可通过助手检索该库。新增库保存后即可上传文档。
      </p>
      <div className="field-grid">
        <SessionPicker
          client={client}
          adapters={config.adapters}
          suggestions={[...draftSessions, ...Object.keys(bindings)]}
          session={session}
          onChange={setSession}
        />
        <label className="field">
          <span className="field-name">使用的知识库</span>
          <select
            disabled={!isMainSession(session)}
            value={bindings[session] ?? ""}
            onChange={(event) => bind(event.target.value)}
          >
            <option value="">沿用默认访问规则</option>
            <option value="disabled">停用知识库</option>
            <option value="default">{DEFAULT_NAME}</option>
            {Object.entries(libraries).map(([key, library]) => (
              <option key={key} value={key}>
                {library.name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {Object.entries(bindings).map(([key, library]) => (
        <div className="inline" key={key}>
          <button type="button" onClick={() => setSession(key)}>
            {sessionTitle(key)}
          </button>
          <span className="muted">{label(library)}</span>
        </div>
      ))}
    </section>
  );
}
