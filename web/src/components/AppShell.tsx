import { LoadingBar } from "./LoadingBar";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  ChevronRight,
  CloudDownload,
  MessageSquare,
  RotateCw,
} from "lucide-react";
import { NAV_GROUPS, pageMeta } from "../navigation";
import { UiLanguageSelect } from "./LanguageSettings";
import type { PageId } from "../navigation";

export function AppShell({
  page,
  onNavigate,
  pendingApprovals,
  online,
  version,
  onLogout,
  onOpenChat,
  onRestart,
  restarting,
  restartDisabled,
  restartHint,
  onCheckUpdate,
  updateAvailable,
  children,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
  pendingApprovals: number;
  online: boolean;
  version: string;
  onLogout: () => void;
  onOpenChat: () => void;
  onRestart: () => void;
  restarting: boolean;
  restartDisabled: boolean;
  restartHint: string;
  onCheckUpdate: () => void;
  updateAvailable: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation("shell");
  const meta = pageMeta(page);
  return (
    <div className="layout">
      <aside>
        <a
          className="brand"
          href="#/overview"
          onClick={(event) => {
            event.preventDefault();
            onNavigate("overview");
          }}
        >
          <span className="brand-mark">
            <img src="/icon.png" alt="" />
          </span>
          <span>
            ShiritaiBot<small>{t("brand.console")}</small>
          </span>
        </a>
        <nav aria-label={t("nav.main")}>
          {NAV_GROUPS.map((group) => (
            <div
              className="nav-group"
              key={group.id}
              role="group"
              aria-label={t(`nav.${group.id}`)}
            >
              <div className="nav-group-title" aria-hidden="true">
                {t(`nav.${group.id}`)}
              </div>
              {group.pages.map(({ id, icon: Icon }) => (
                <button
                  type="button"
                  key={id}
                  title={t(`pages.${id}.label`)}
                  className={meta.id === id ? "active" : ""}
                  aria-current={meta.id === id ? "page" : undefined}
                  onClick={() => onNavigate(id)}
                >
                  <Icon size={18} aria-hidden="true" />
                  <span>{t(`pages.${id}.label`)}</span>
                  {id === "approvals" && pendingApprovals > 0 && (
                    <b>{pendingApprovals}</b>
                  )}
                </button>
              ))}
            </div>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status" role="status">
            <span
              className={"dot " + (online ? "on" : "")}
              aria-hidden="true"
            />
            {restarting ? (
              <LoadingBar compact label={t("status.restarting")} />
            ) : online ? (
              t("status.online")
            ) : (
              t("status.offline")
            )}
            <small>{version}</small>
          </div>
          <button
            type="button"
            className="restart-button"
            onClick={onRestart}
            disabled={restartDisabled || restarting}
            title={restartHint}
            aria-label={restarting ? t("restart.busy") : t("restart.label")}
          >
            {restarting ? (
              <LoadingBar compact label={t("status.restarting")} />
            ) : (
              <>
                <RotateCw size={16} aria-hidden="true" />
                <span>{t("restart.label")}</span>
              </>
            )}
          </button>
        </div>
      </aside>
      <main id="main-content">
        <header className="console-header">
          <div className="console-crumb">
            <span className="muted">{t("header.crumb")}</span>
            <ChevronRight size={13} aria-hidden="true" />
            <span aria-current="page">{t(`pages.${meta.id}.label`)}</span>
          </div>
          <div className="console-actions">
            <span className="local-badge">{t("header.localOnly")}</span>
            <button
              type="button"
              className="chat-mode-button"
              aria-label={t("header.chatMode")}
              onClick={onOpenChat}
            >
              <MessageSquare size={14} aria-hidden="true" />
              <span className="console-action-label">
                {t("header.chatMode")}
              </span>
            </button>
            <button
              type="button"
              className="update-button"
              aria-label={t("header.checkUpdate")}
              onClick={onCheckUpdate}
              title={
                updateAvailable
                  ? t("header.updateAvailable")
                  : t("header.checkUpdate")
              }
            >
              <CloudDownload size={14} aria-hidden="true" />
              <span className="console-action-label">
                {t("header.checkUpdate")}
              </span>
              {updateAvailable && (
                <span
                  className="update-dot"
                  aria-label={t("header.newVersion")}
                />
              )}
            </button>
            <UiLanguageSelect compact />
            <button type="button" onClick={onLogout}>
              {t("header.logout")}
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-title">
            <div>
              <h1>{t(`pages.${meta.id}.label`)}</h1>
              <p className="subtitle">{t(`pages.${meta.id}.subtitle`)}</p>
            </div>
          </div>
          {children}
        </div>
        <footer>
          ShiritaiBot <span>·</span> {t("footer.localData")}
          <span className="muted">{t("footer.tagline")}</span>
        </footer>
      </main>
    </div>
  );
}
