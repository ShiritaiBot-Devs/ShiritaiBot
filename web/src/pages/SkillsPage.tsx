import { LoadingBar } from "../components/LoadingBar";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { Field } from "../components/FieldForm";
import type { SkillInfo } from "../api/types";
import type { ConfigPageProps } from "./common";
import i18n from "../i18n";

export function SkillsPage({
  client,
  config,
  setConfig,
  act,
}: ConfigPageProps) {
  const queryClient = useQueryClient();
  const listing = useQuery({
    queryKey: ["skills"],
    queryFn: () => client.skills(),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const [installing, setInstalling] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [replace, setReplace] = useState(false);
  const [working, setWorking] = useState(false);
  const rows = listing.data ?? [];
  const current = rows.find((row) => row.name === selected) ?? rows[0];
  const detail = useQuery({
    queryKey: ["skills", current?.name, current?.source],
    queryFn: () => client.skill(current!.name),
    enabled: Boolean(current),
  });
  const skills = config.skills;

  function setSkills(patch: Partial<typeof skills>) {
    setConfig({ ...config, skills: { ...skills, ...patch } });
  }

  function toggle(name: string, enabled: boolean) {
    const rest = skills.disabled.filter((item) => item !== name);
    setSkills({ disabled: enabled ? rest : [...rest, name] });
  }

  async function mutate(work: () => Promise<SkillInfo[]>, message: string) {
    setWorking(true);
    try {
      await act(async () => {
        queryClient.setQueryData(["skills"], await work());
        setInstalling(false);
        setRemoving(null);
        setFile(null);
      }, message);
    } finally {
      setWorking(false);
    }
  }

  return (
    <>
      <section className="panel">
        <h2>{i18n.t("skills:settings.title")}</h2>
        <Field
          spec={{
            key: "enabled",
            label: i18n.t("skills:settings.enabled.label"),
            kind: "boolean",
            help: i18n.t("skills:settings.enabled.help"),
          }}
          value={skills.enabled}
          onChange={(enabled) => setSkills({ enabled: Boolean(enabled) })}
        />
        <Field
          spec={{
            key: "inject",
            label: i18n.t("skills:settings.list.label"),
            kind: "boolean",
            help: i18n.t("skills:settings.list.help"),
          }}
          value={skills.inject}
          onChange={(inject) => setSkills({ inject: Boolean(inject) })}
        />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>{i18n.t("skills:list.title")}</h2>
          <button
            type="button"
            disabled={working}
            onClick={() => setInstalling(!installing)}
          >
            <Plus size={14} />
            {i18n.t("skills:install.title")}
          </button>
        </div>
        {installing && (
          <form
            className="config-editor"
            onSubmit={(event) => {
              event.preventDefault();
              if (file)
                void mutate(
                  () => client.installSkill(file, replace),
                  i18n.t("skills:installed"),
                );
            }}
          >
            <p className="muted">{i18n.t("skills:install.hint")}</p>
            <label className="field">
              {i18n.t("skills:install.file")}
              <input
                aria-label={i18n.t("skills:install.file")}
                type="file"
                accept=".skill,.zip,.md"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <label className="field">
              <span>
                <input
                  type="checkbox"
                  checked={replace}
                  onChange={(event) => setReplace(event.target.checked)}
                />{" "}
                {i18n.t("skills:install.replace")}
              </span>
            </label>
            <div className="actions">
              <button type="submit" disabled={working || !file}>
                {i18n.t("skills:install.submit")}
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => setInstalling(false)}
              >
                {i18n.t("skills:cancel")}
              </button>
            </div>
          </form>
        )}
        {removing && (
          <div
            role="dialog"
            aria-modal="true"
            aria-label={i18n.t("skills:remove.dialog")}
            className="config-editor"
          >
            <h3>{i18n.t("skills:remove.title", { name: removing })}</h3>
            <p>{i18n.t("skills:remove.hint")}</p>
            <div className="actions">
              <button
                type="button"
                className="danger"
                disabled={working}
                onClick={() =>
                  void mutate(
                    () => client.removeSkill(removing),
                    i18n.t("skills:removed"),
                  )
                }
              >
                {i18n.t("skills:remove.confirm")}
              </button>
              <button
                type="button"
                disabled={working}
                onClick={() => setRemoving(null)}
              >
                {i18n.t("skills:cancel")}
              </button>
            </div>
          </div>
        )}
        {listing.isPending ? (
          <LoadingBar label={i18n.t("skills:loading")} />
        ) : listing.isError ? (
          <p role="alert">{listing.error.message}</p>
        ) : !rows.length ? (
          <EmptyState text={i18n.t("skills:empty")} />
        ) : (
          <div className="config-workspace">
            <section
              className="config-sidebar"
              aria-label={i18n.t("skills:list.title")}
            >
              {rows.map((row) => {
                const enabled = !skills.disabled.includes(row.name);
                return (
                  <button
                    type="button"
                    key={row.name}
                    className={
                      "config-item" +
                      (row.name === current?.name ? " selected" : "")
                    }
                    onClick={() => setSelected(row.name)}
                  >
                    <span
                      className={"dot " + (enabled && !row.error ? "on" : "")}
                    />
                    <span>
                      <strong>{row.name}</strong>
                      <small>
                        {row.source === "bundled"
                          ? i18n.t("skills:source.builtin")
                          : i18n.t("skills:source.installed")}
                        {row.overrides ? i18n.t("skills:flags.overrides") : ""}
                        {row.error
                          ? i18n.t("skills:flags.invalid")
                          : enabled
                            ? ""
                            : i18n.t("skills:flags.disabled")}
                      </small>
                    </span>
                  </button>
                );
              })}
            </section>
            <section className="config-editor">
              {current && (
                <>
                  <div className="panel-head">
                    <div>
                      <h2>{current.name}</h2>
                      <p className="muted">{current.description}</p>
                    </div>
                    {current.source === "installed" && (
                      <button
                        type="button"
                        className="danger"
                        disabled={working}
                        onClick={() => setRemoving(current.name)}
                      >
                        <Trash2 size={14} />
                        {i18n.t("skills:remove.button")}
                      </button>
                    )}
                  </div>
                  {current.error && <p role="alert">{current.error}</p>}
                  <Field
                    spec={{
                      key: "skill-enabled",
                      label: i18n.t("skills:item.enabled.label"),
                      kind: "boolean",
                      help: i18n.t("skills:item.enabled.help"),
                    }}
                    value={!skills.disabled.includes(current.name)}
                    onChange={(value) => toggle(current.name, Boolean(value))}
                  />
                  <p className="muted">
                    {i18n.t("skills:item.path", { path: current.path })}
                  </p>
                  {detail.data && (
                    <>
                      <h3>{i18n.t("skills:item.files")}</h3>
                      <ul className="plain-list">
                        {detail.data.files.map((item) => (
                          <li key={item.path}>
                            <code>{item.path}</code>{" "}
                            <small className="muted">
                              {i18n.t("skills:item.bytes", {
                                count: item.bytes,
                              })}
                            </small>
                          </li>
                        ))}
                      </ul>
                      <h3>SKILL.md</h3>
                      <pre>{detail.data.content}</pre>
                    </>
                  )}
                  {detail.isError && <p role="alert">{detail.error.message}</p>}
                </>
              )}
            </section>
          </div>
        )}
      </section>
    </>
  );
}
