import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, Folder } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Action } from "../pages/common";
import { desktop, type DesktopPreferences } from "./DesktopTitleBar";

export function DesktopSettings({ act }: { act: Action }) {
  const { t } = useTranslation("settings");
  const cache = useQueryClient();
  const key = ["desktop-preferences"];
  const query = useQuery({
    queryKey: key,
    queryFn: () => desktop!.preferences(),
    enabled: Boolean(desktop),
  });
  const preferences = query.data;
  if (!desktop || !preferences) return null;

  function save<K extends "closeBehavior" | "notifications">(
    keyName: K,
    value: DesktopPreferences[K],
  ) {
    void act(async () => {
      cache.setQueryData(key, await desktop!.savePreference(keyName, value));
    }, "");
  }
  return (
    <section className="settings-group">
      <h2>{t("desktop.title")}</h2>
      <div className="field-grid">
        <label>
          <span>{t("desktop.closeBehavior")}</span>
          <select
            value={preferences.closeBehavior}
            onChange={(event) =>
              save(
                "closeBehavior",
                event.target.value as DesktopPreferences["closeBehavior"],
              )
            }
          >
            <option value="ask">{t("desktop.ask")}</option>
            <option value="quit">{t("desktop.quit")}</option>
            <option value="tray">{t("desktop.tray")}</option>
          </select>
        </label>
        <div className="desktop-notification-setting">
          <label className="field field-toggle desktop-notification-toggle">
            <span className="field-name">{t("desktop.notifications")}</span>
            <input
              type="checkbox"
              checked={preferences.notifications}
              onChange={(event) => save("notifications", event.target.checked)}
            />
          </label>
          {desktop.platform === "win32" && (
            <button
              type="button"
              onClick={() => desktop?.action("notification-settings")}
            >
              <Bell size={15} />
              {t("desktop.windowsNotifications")}
            </button>
          )}
        </div>
        <div className="desktop-data-setting">
          <strong>{t("desktop.dataDirectory")}</strong>
          <code className="workspace-path">{preferences.dataDirectory}</code>
          <div className="inline">
            <button
              type="button"
              onClick={() =>
                void act(async () => {
                  const selected = await desktop?.selectDataFolder(
                    preferences.dataDirectory,
                  );
                  if (selected) cache.setQueryData(key, selected);
                }, "")
              }
            >
              <Folder size={15} />
              {t("desktop.chooseFolder")}
            </button>
            {preferences.dataDirectory !== preferences.activeDirectory && (
              <button type="button" onClick={() => desktop?.action("relaunch")}>
                {t("desktop.relaunch")}
              </button>
            )}
          </div>
          {preferences.dataDirectory !== preferences.activeDirectory && (
            <small className="muted">{t("desktop.restartHint")}</small>
          )}
        </div>
      </div>
    </section>
  );
}
