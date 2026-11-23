import { useState } from "react";
import { isMainSession, sessionTitle } from "../lib/sessions";
import type { SessionConfigPageProps } from "../pages/common";
import { SessionPicker } from "./SessionPicker";
import i18n from "../i18n";

const DEFAULT_NAME = i18n.t("knowledge:defaultLibrary");

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
    if (library === "disabled") return i18n.t("knowledge:libraries.disable");
    if (library === "default") return DEFAULT_NAME;
    return libraries[library]?.name ?? library;
  }

  return (
    <section className="panel">
      <h2>{i18n.t("knowledge:libraries.settings")}</h2>
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
          aria-label={i18n.t("knowledge:libraries.idLabel")}
          placeholder={i18n.t("knowledge:libraries.idPlaceholder")}
          maxLength={64}
          value={id}
          onChange={(event) => setId(event.target.value)}
          required
        />
        <input
          aria-label={i18n.t("knowledge:libraries.nameLabel")}
          placeholder={i18n.t("knowledge:libraries.namePlaceholder")}
          maxLength={80}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <button type="submit" disabled={!validId || !name.trim()}>
          {i18n.t("knowledge:libraries.add")}
        </button>
      </form>
      {id && !validId && (
        <p className="muted">{i18n.t("knowledge:libraries.idHint")}</p>
      )}
      {Object.entries(libraries).map(([key, library]) => (
        <div className="inline" key={key}>
          <code>{key}</code>
          <input
            aria-label={i18n.t("knowledge:libraries.displayNameOf", { key })}
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
            title={i18n.t("knowledge:libraries.removeHint")}
            onClick={() => {
              const next = { ...libraries };
              delete next[key];
              update({ libraries: next });
            }}
          >
            {i18n.t("knowledge:libraries.remove")}
          </button>
        </div>
      ))}
      <h3>{i18n.t("knowledge:bindings.title")}</h3>
      <p className="muted">{i18n.t("knowledge:bindings.hint")}</p>
      <div className="field-grid">
        <SessionPicker
          client={client}
          adapters={config.adapters}
          suggestions={[
            currentSession,
            ...draftSessions,
            ...Object.keys(bindings),
          ]}
          session={session}
          onChange={setSession}
        />
        <label className="field">
          <span className="field-name">
            {i18n.t("knowledge:bindings.library")}
          </span>
          <select
            disabled={!isMainSession(session)}
            value={bindings[session] ?? ""}
            onChange={(event) => bind(event.target.value)}
          >
            <option value="">{i18n.t("knowledge:bindings.default")}</option>
            <option value="disabled">
              {i18n.t("knowledge:libraries.disable")}
            </option>
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
