import { useState } from "react";
import { ChevronDown, ChevronRight, Pin, Plus } from "lucide-react";
import type { SessionGroup, SessionGroupKey } from "../lib/sessions";
import { ChatSessionActions, type ChatActions } from "./ChatSessionActions";

interface ChatSidebarProps extends ChatActions {
  groups: SessionGroup[];
  selected: string;
  running: Set<string>;
  pending: Set<string>;
  onSelect: (id: string) => void;
  onNew: () => void;
}

export function ChatSidebar({
  groups,
  selected,
  running,
  pending,
  onSelect,
  onNew,
  onUpdate,
  onDelete,
}: ChatSidebarProps) {
  const [closed, setClosed] = useState<Set<SessionGroupKey>>(
    () => new Set(["private", "group", "background"]),
  );
  function toggle(key: SessionGroupKey) {
    setClosed((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  return (
    <div className="chat-sidebar">
      <div className="chat-brand">
        <strong>ShiritaiBot</strong>
        <small>对话</small>
      </div>
      <button type="button" className="chat-new" onClick={onNew}>
        <Plus size={15} aria-hidden="true" />
        新对话
      </button>
      <nav
        className="chat-groups"
        aria-label="会话列表"
        onScroll={(event) => {
          event.currentTarget
            .querySelector<HTMLElement>(".chat-actions-menu:popover-open")
            ?.hidePopover();
        }}
      >
        {groups.map((group) => (
          <div className="chat-group" key={group.key}>
            <h2>
              <button
                type="button"
                className="chat-group-toggle"
                aria-expanded={!closed.has(group.key)}
                aria-controls={`chat-list-${group.key}`}
                onClick={() => toggle(group.key)}
              >
                {closed.has(group.key) ? (
                  <ChevronRight size={13} />
                ) : (
                  <ChevronDown size={13} />
                )}
                {group.label}
                <span className="chat-group-count">{group.items.length}</span>
              </button>
            </h2>
            <div id={`chat-list-${group.key}`} hidden={closed.has(group.key)}>
              {group.items.map((item) => (
                <div
                  className={
                    "chat-row" + (item.id === selected ? " active" : "")
                  }
                  key={item.id}
                >
                  <button
                    type="button"
                    className="chat-session"
                    aria-current={item.id === selected ? "page" : undefined}
                    title={item.title}
                    aria-label={item.title}
                    aria-description={item.pinned ? "已置顶" : undefined}
                    onClick={() => onSelect(item.id)}
                  >
                    {item.pinned && <Pin size={12} aria-label="已置顶" />}
                    <span className="chat-session-title">{item.title}</span>
                    {running.has(item.id) && (
                      <span className="session-dot" aria-label="运行中" />
                    )}
                    {pending.has(item.id) && (
                      <span className="session-badge" aria-label="待审批">
                        !
                      </span>
                    )}
                  </button>
                  {item.id.startsWith("web:") && (
                    <ChatSessionActions
                      item={item}
                      onUpdate={onUpdate}
                      onDelete={onDelete}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </nav>
    </div>
  );
}
