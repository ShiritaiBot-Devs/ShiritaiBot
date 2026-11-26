import { useId, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Folder, Monitor } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api/client";
import type { Action } from "../pages/common";
import { desktop } from "./DesktopTitleBar";

interface ChatToolProps {
  client: ApiClient;
  session: string;
}

export function ChatWorkspacePicker({
  client,
  session,
  act,
  busy,
  onChange,
}: ChatToolProps & {
  act: Action;
  busy: boolean;
  onChange: () => void;
}) {
  const { t } = useTranslation(["composer", "common"]);
  const cache = useQueryClient();
  const key = ["chat-workspace", session];
  const workspace = useQuery({
    queryKey: key,
    queryFn: () => client.chatWorkspace(session),
  });
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useId();
  const [path, setPath] = useState("");
  const [suggestions, setSuggestions] = useState<string[] | null>(null);
  const [saving, setSaving] = useState(false);

  async function save(value: string, create = false) {
    if (saving) return;
    setSaving(true);
    try {
      await act(async () => {
        const result = await client.setChatWorkspace(session, value, create);
        if ("missing" in result) {
          setPath(result.path);
          setSuggestions(result.suggestions);
          if (!dialog.current?.open) dialog.current?.showModal();
        } else {
          if (result.path !== workspace.data?.path) onChange();
          cache.setQueryData(key, result);
          dialog.current?.close();
        }
      }, "");
    } finally {
      setSaving(false);
    }
  }

  async function open() {
    setSuggestions(null);
    if (desktop) {
      const folder = await desktop.selectFolder(workspace.data?.path);
      if (folder) await save(folder);
    } else {
      setPath(workspace.data?.custom ? workspace.data.path : "");
      dialog.current?.showModal();
    }
  }

  const label = workspace.data?.custom
    ? workspace.data.path.split(/[\\/]/).filter(Boolean).at(-1) ||
      workspace.data.path
    : t("defaultWorkspace");
  return (
    <div className="composer-workspace">
      <button
        type="button"
        className="composer-workspace-picker"
        disabled={busy || saving}
        aria-label={t("workspace")}
        title={workspace.data?.path || t("workspace")}
        onClick={() => void open()}
      >
        {desktop ? <Monitor size={15} /> : <Folder size={15} />}
        <span>{label}</span>
      </button>
      {desktop && workspace.data?.custom && (
        <button
          type="button"
          disabled={busy || saving}
          onClick={() => void save("")}
        >
          {t("resetWorkspace")}
        </button>
      )}
      <dialog
        ref={dialog}
        aria-labelledby={heading}
        className="workspace-dialog"
        onKeyDown={(event) => {
          if (
            event.key === "Enter" &&
            event.target instanceof HTMLInputElement &&
            !suggestions
          ) {
            event.preventDefault();
            void save(path);
          }
        }}
      >
        <h2 id={heading}>
          {suggestions ? t("workspaceMissing") : t("workspace")}
        </h2>
        {suggestions ? (
          <>
            <p className="workspace-path">{path}</p>
            {suggestions.length > 0 && <p>{t("workspaceSuggestions")}</p>}
            <ul>
              {suggestions.map((suggestion) => (
                <li key={suggestion}>
                  <span className="workspace-path">{suggestion}</span>
                  <button
                    type="button"
                    disabled={saving}
                    onClick={() => void save(suggestion)}
                  >
                    {t("useWorkspace")}
                  </button>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <label>
            {t("serverWorkspace")}{" "}
            <input
              value={path}
              onChange={(event) => setPath(event.target.value)}
            />
          </label>
        )}
        <div className="workspace-dialog-actions">
          <button type="button" onClick={() => dialog.current?.close()}>
            {t("common:cancel")}
          </button>
          {suggestions ? (
            <button
              type="button"
              className="primary"
              disabled={saving}
              onClick={() => void save(path, true)}
            >
              {t("createWorkspace")}
            </button>
          ) : (
            <>
              {workspace.data?.custom && (
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void save("")}
                >
                  {t("resetWorkspace")}
                </button>
              )}
              <button
                type="button"
                className="primary"
                disabled={saving || !path.trim()}
                onClick={() => void save(path)}
              >
                {t("useWorkspace")}
              </button>
            </>
          )}
        </div>
      </dialog>
    </div>
  );
}

export function ChatContextMeter({ client, session }: ChatToolProps) {
  const { t, i18n } = useTranslation("composer");
  const [open, setOpen] = useState(false);
  const context = useQuery({
    queryKey: ["chat-context", session],
    queryFn: () => client.chatContext(session),
    refetchInterval: 3000,
  });
  const usage = context.data;
  const fraction = usage ? usage.used / usage.limit : 0;
  const percent = new Intl.NumberFormat(i18n.language, {
    style: "percent",
  }).format(fraction);
  const format = new Intl.NumberFormat(i18n.language);
  const details = useId();
  return (
    <div className="composer-context">
      <button
        type="button"
        aria-label={t("contextWindow")}
        aria-describedby={details}
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        onBlur={() => setOpen(false)}
      >
        <span
          aria-hidden="true"
          className="context-ring"
          style={{
            background: `conic-gradient(var(--accent) ${Math.min(fraction, 1)}turn, var(--border-strong) 0)`,
          }}
        />
      </button>
      <div
        className={"context-details" + (open ? " is-open" : "")}
        role="tooltip"
        id={details}
      >
        <strong>{t("contextWindow")}</strong>
        {usage ? (
          <>
            <span>{usage.model}</span>
            <span>
              {t(
                usage.unit === "characters"
                  ? usage.estimated
                    ? "estimatedContextCharacters"
                    : "contextCharacters"
                  : usage.estimated
                    ? "estimatedContextTokens"
                    : "contextTokens",
                {
                  used: format.format(usage.used),
                  total: format.format(usage.limit),
                  percent,
                },
              )}
            </span>
          </>
        ) : (
          <span>{t("noContextUsage")}</span>
        )}
      </div>
    </div>
  );
}
