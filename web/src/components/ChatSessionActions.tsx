import { useEffect, useId, useRef, useState } from "react";
import { Ellipsis, Pencil, Pin, PinOff, Trash2 } from "lucide-react";
import type { SessionItem } from "../lib/sessions";
import type { WebChat } from "../api/types";
import { useNotice } from "../hooks/useNotice";

export interface ChatActions {
  onUpdate: (
    id: string,
    patch: Partial<Pick<WebChat, "title" | "pinned">>,
  ) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}

export function ChatSessionActions({
  item,
  onUpdate,
  onDelete,
}: ChatActions & { item: SessionItem }) {
  const menuId = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const [editing, setEditing] = useState<"rename" | "delete" | null>(null);
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const { fail } = useNotice();

  useEffect(() => {
    if (!editing || !dialog.current) return;
    const el = dialog.current;
    el.showModal();
    el.querySelector("input")?.select();
    return () => el.close();
  }, [editing]);

  function edit(mode: "rename" | "delete") {
    menu.current?.hidePopover();
    setTitle(item.title);
    setError("");
    setEditing(mode);
  }
  async function submit() {
    setSaving(true);
    try {
      if (editing === "rename")
        await onUpdate(item.id, { title: title.trim() });
      else await onDelete(item.id);
      setEditing(null);
      trigger.current?.focus();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }
  async function pin() {
    menu.current?.hidePopover();
    setSaving(true);
    try {
      await onUpdate(item.id, { pinned: !item.pinned });
    } catch (err) {
      fail(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <button
        className="chat-more"
        type="button"
        ref={trigger}
        popoverTarget={menuId}
        aria-label={`${item.title}的更多操作`}
        aria-haspopup="menu"
        disabled={saving}
      >
        <Ellipsis size={16} />
      </button>
      <div
        ref={menu}
        id={menuId}
        popover="auto"
        role="menu"
        aria-label="对话操作"
        className="chat-actions-menu"
        onBeforeToggle={(event) => {
          if (event.newState !== "open" || !trigger.current) return;
          const el = event.currentTarget,
            rect = trigger.current.getBoundingClientRect();
          el.style.left =
            Math.max(8, Math.min(rect.left, innerWidth - 144)) + "px";
          const above = innerHeight - rect.bottom < 140 && rect.top > 140;
          el.style.top = above ? "auto" : rect.bottom + 3 + "px";
          el.style.bottom = above ? innerHeight - rect.top + 3 + "px" : "auto";
        }}
        onToggle={(event) => {
          if (event.newState === "open")
            event.currentTarget
              .querySelector("button")
              ?.focus({ preventScroll: true });
        }}
        onKeyDown={(event) => {
          const options = Array.from(
            event.currentTarget.querySelectorAll("button"),
          );
          const index = options.indexOf(
            document.activeElement as HTMLButtonElement,
          );
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            options[
              (index + (event.key === "ArrowDown" ? 1 : -1) + options.length) %
                options.length
            ].focus();
          }
        }}
      >
        <button type="button" role="menuitem" onClick={() => edit("rename")}>
          <Pencil size={15} />
          重命名
        </button>
        <button type="button" role="menuitem" onClick={() => void pin()}>
          {item.pinned ? <PinOff size={15} /> : <Pin size={15} />}{" "}
          {item.pinned ? "取消置顶" : "置顶"}
        </button>
        <button
          type="button"
          className="delete-chat"
          role="menuitem"
          onClick={() => edit("delete")}
        >
          <Trash2 size={15} />
          删除
        </button>
      </div>
      {editing && (
        <dialog
          className="adapter-dialog chat-edit-dialog"
          ref={dialog}
          aria-label={editing === "rename" ? "重命名对话" : "删除对话"}
          onCancel={(event) => {
            event.preventDefault();
            if (!saving) setEditing(null);
          }}
        >
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!saving) void submit();
            }}
          >
            <h2>{editing === "rename" ? "重命名对话" : "删除对话"}</h2>
            {editing === "rename" ? (
              <label className="field">
                <span>对话名称</span>
                <input
                  value={title}
                  autoFocus
                  maxLength={100}
                  onChange={(event) => setTitle(event.target.value)}
                />
              </label>
            ) : (
              <p>删除「{item.title}」及其聊天记录？此操作无法撤销。</p>
            )}
            {error && <p role="alert">{error}</p>}
            <div className="actions adapter-dialog-actions">
              <button
                type="button"
                disabled={saving}
                autoFocus={editing === "delete"}
                onClick={() => setEditing(null)}
              >
                取消
              </button>
              <button
                type="submit"
                className={editing === "delete" ? "danger" : "primary"}
                disabled={saving || (editing === "rename" && !title.trim())}
              >
                {editing === "delete" ? "删除" : "保存"}
              </button>
            </div>
          </form>
        </dialog>
      )}
    </>
  );
}
