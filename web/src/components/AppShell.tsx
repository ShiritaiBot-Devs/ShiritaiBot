import { LoadingBar } from "./LoadingBar";
import type { ReactNode } from "react";
import {
  ChevronRight,
  CloudDownload,
  MessageSquare,
  RotateCw,
} from "lucide-react";
import { PAGES, pageMeta } from "../navigation";
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
            ShiritaiBot<small>控制台</small>
          </span>
        </a>
        <nav aria-label="主导航">
          {PAGES.map(({ id, label, icon: Icon }) => (
            <button
              type="button"
              key={id}
              title={label}
              className={page === id ? "active" : ""}
              aria-current={page === id ? "page" : undefined}
              onClick={() => onNavigate(id)}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              {id === "approvals" && pendingApprovals > 0 && (
                <b>{pendingApprovals}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status" role="status">
            <span
              className={"dot " + (online ? "on" : "")}
              aria-hidden="true"
            />
            {restarting ? (
              <LoadingBar compact label="正在重启…" />
            ) : online ? (
              "系统运行中"
            ) : (
              "连接未就绪"
            )}
            <small>{version}</small>
          </div>
          <button
            type="button"
            className="restart-button"
            onClick={onRestart}
            disabled={restartDisabled || restarting}
            title={restartHint}
            aria-label={restarting ? "正在重启" : "重启"}
          >
            {restarting ? (
              <LoadingBar compact label="正在重启…" />
            ) : (
              <>
                <RotateCw size={16} aria-hidden="true" />
                <span>重启</span>
              </>
            )}
          </button>
        </div>
      </aside>
      <main id="main-content">
        <header className="console-header">
          <div className="console-crumb">
            <span className="muted">本机控制台</span>
            <ChevronRight size={13} aria-hidden="true" />
            <span aria-current="page">{meta.label}</span>
          </div>
          <div className="console-actions">
            <span className="local-badge">仅本机</span>
            <button
              type="button"
              className="chat-mode-button"
              aria-label="对话模式"
              onClick={onOpenChat}
            >
              <MessageSquare size={14} aria-hidden="true" />
              <span className="console-action-label">对话模式</span>
            </button>
            <button
              type="button"
              className="update-button"
              aria-label="检查更新"
              onClick={onCheckUpdate}
              title={updateAvailable ? "有可用的新版本" : "检查更新"}
            >
              <CloudDownload size={14} aria-hidden="true" />
              <span className="console-action-label">检查更新</span>
              {updateAvailable && (
                <span className="update-dot" aria-label="有新版本" />
              )}
            </button>
            <button type="button" onClick={onLogout}>
              退出
            </button>
          </div>
        </header>
        <div className="content">
          <div className="page-title">
            <div>
              <h1>{meta.label}</h1>
              <p className="subtitle">{meta.subtitle}</p>
            </div>
          </div>
          {children}
        </div>
        <footer>
          ShiritaiBot <span>·</span> 本地数据
          <span className="muted">明确权限 · 持续记忆</span>
        </footer>
      </main>
    </div>
  );
}
