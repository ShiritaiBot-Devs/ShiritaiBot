import { Copy, Minus, Square, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

declare global {
  interface Window {
    shiritaiDesktop?: {
      platform: string;
      action: (
        name:
          | "minimize"
          | "maximize"
          | "close"
          | "releases"
          | "notification-settings"
          | "relaunch",
      ) => void;
      preferences: () => Promise<DesktopPreferences>;
      savePreference: <K extends "closeBehavior" | "notifications">(
        key: K,
        value: DesktopPreferences[K],
      ) => Promise<DesktopPreferences>;
      selectFolder: (current?: string) => Promise<string | null>;
      selectDataFolder: (current: string) => Promise<DesktopPreferences | null>;
      onMaximized: (callback: (maximized: boolean) => void) => () => void;
    };
  }
}

export interface DesktopPreferences {
  closeBehavior: "ask" | "quit" | "tray";
  notifications: boolean;
  dataDirectory: string;
  activeDirectory: string;
}

export const desktop = window.shiritaiDesktop;

if (desktop) document.documentElement.dataset.desktop = desktop.platform;

export function DesktopTitleBar() {
  const { t } = useTranslation("shell");
  const [maximized, setMaximized] = useState(false);
  useEffect(() => desktop?.onMaximized(setMaximized), []);
  if (!desktop || desktop.platform === "darwin") return null;

  return (
    <div className="desktop-titlebar">
      <span className="desktop-titlebar-brand">
        <img src="/icon.png" alt="" width={19} height={19} />
        ShiritaiBot
      </span>
      <div className="desktop-window-actions">
        <button
          type="button"
          aria-label={t("desktop.minimize")}
          title={t("desktop.minimize")}
          onClick={() => desktop.action("minimize")}
        >
          <Minus size={15} />
        </button>
        <button
          type="button"
          aria-label={t(maximized ? "desktop.restore" : "desktop.maximize")}
          title={t(maximized ? "desktop.restore" : "desktop.maximize")}
          onClick={() => desktop.action("maximize")}
        >
          {maximized ? <Copy size={13} /> : <Square size={12} />}
        </button>
        <button
          type="button"
          aria-label={t("desktop.close")}
          title={t("desktop.close")}
          onClick={() => desktop.action("close")}
        >
          <X size={16} />
        </button>
      </div>
    </div>
  );
}

export function DesktopSidebarDrag() {
  return desktop?.platform === "darwin" ? (
    <div className="desktop-sidebar-drag" aria-hidden="true" />
  ) : null;
}
