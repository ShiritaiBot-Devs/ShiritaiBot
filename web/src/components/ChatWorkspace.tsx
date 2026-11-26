import { LoadingBar } from "./LoadingBar";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Menu } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { WebChat } from "../api/types";
import { draftKey, modelKey, writeValue } from "../lib/composer";
import { AgentTimeline } from "./AgentTimeline";
import { ApprovalCard } from "./ApprovalCard";
import { BrandWordmark } from "./BrandWordmark";
import { ChatComposer } from "./ChatComposer";
import { ChatSidebar } from "./ChatSidebar";
import { useChatOutbox } from "../hooks/useChatOutbox";
import { QueryErrorNotice } from "./QueryErrorNotice";
import type { Action } from "../pages/common";
import {
  runningSessions,
  sessionActivity,
  sessionGroups,
  sessionTitle,
} from "../lib/sessions";
import i18n from "../i18n";

export interface ChatWorkspaceProps {
  client: ApiClient;
  act: Action;
  assistantName: string;
  session: string;
  onSelectSession: (id: string) => void;
  onCreateSession: (id: string) => void;
  onForgetSession: (id: string) => void;
  draftSessions: string[];
  onOpenSettings: () => void;
  setup?: ReactNode;
}

export function ChatWorkspace({
  client,
  act,
  assistantName,
  session,
  onSelectSession,
  onCreateSession,
  onForgetSession,
  draftSessions,
  onOpenSettings,
  setup,
}: ChatWorkspaceProps) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);
  const cache = useQueryClient();
  const webChats = useQuery({
    queryKey: ["web-chats"],
    queryFn: () => client.webChats(),
    refetchInterval: 10000,
  });

  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
    refetchInterval: 10000,
  });
  const turns = useQuery({
    queryKey: ["records", "turn"],
    queryFn: () => client.records("turn"),
    refetchInterval: 2500,
  });
  const history = useQuery({
    queryKey: ["history", session],
    queryFn: () => client.history(session),
    refetchInterval: 2500,
  });
  const approvals = useQuery({
    queryKey: ["records", "approval"],
    queryFn: () => client.records("approval"),
    refetchInterval: 4000,
  });

  const ids = Array.from(
    new Set([
      ...draftSessions,
      ...(webChats.data ?? []).map((row) => row.id),
      ...(sessions.data ?? [])
        .map((row) => row.id)
        .filter((id) => !id.startsWith("web:")),
      session,
    ]),
  );
  const activity = sessionActivity(turns.data ?? []);
  const titles = new Map(webChats.data?.map((row) => [row.id, row]));
  const groups = sessionGroups(ids, activity, titles);
  useEffect(() => {
    if (
      !webChats.data ||
      !session.startsWith("web:") ||
      draftSessions.includes(session) ||
      webChats.data.some((row) => row.id === session)
    )
      return;
    const fallback =
      webChats.data.find((row) => row.pinned) ?? webChats.data[0];
    if (fallback) onSelectSession(fallback.id);
    else onCreateSession("web:" + Date.now());
  }, [webChats.data, session, draftSessions, onSelectSession, onCreateSession]);
  const running = runningSessions(turns.data ?? []);
  const pending = new Set(
    (approvals.data ?? [])
      .filter((row) => row.status === "pending" && row.reviewer !== "model")
      .map((row) => row.scope),
  );
  const current = (approvals.data ?? []).filter(
    (row) =>
      row.status === "pending" &&
      row.reviewer !== "model" &&
      row.scope === session,
  );
  const { pending: outgoing, send } = useChatOutbox(
    client,
    act,
    session,
    history.data,
  );
  const waiting =
    outgoing.some((row) => !row.sendFailed) || running.has(session);
  const empty =
    history.data?.length === 0 &&
    !outgoing.length &&
    !current.length &&
    !waiting;

  useEffect(() => {
    stickRef.current = true;
    const scroll = scrollRef.current;
    const stream = streamRef.current;
    if (!scroll || !stream) return;
    const observer = new ResizeObserver(() => {
      if (stickRef.current && !empty) scroll.scrollTop = scroll.scrollHeight;
    });
    observer.observe(stream);
    return () => observer.disconnect();
  }, [session, empty]);

  useEffect(() => {
    const element = scrollRef.current;
    if (!element || !stickRef.current || empty) return;
    element.scrollTop = element.scrollHeight;
  }, [history.data, approvals.data, session, outgoing.length, waiting, empty]);

  function resolve(id: string, allow: boolean) {
    setBusy(true);
    void act(
      () => client.resolveApproval(id, allow),
      allow
        ? i18n.t("chat:workspace.allowed")
        : i18n.t("chat:workspace.denied"),
    ).finally(() => setBusy(false));
  }

  function select(id: string) {
    onSelectSession(id);
    setDrawerOpen(false);
  }

  async function updateChat(
    id: string,
    patch: Partial<Pick<WebChat, "title" | "pinned">>,
  ) {
    await client.updateWebChat(id, patch);
    await cache.invalidateQueries({ queryKey: ["web-chats"] });
  }

  async function removeChat(id: string) {
    await client.deleteWebChat(id);
    onForgetSession(id);
    writeValue(draftKey(id), "");
    writeValue(modelKey(id), "");
    cache.removeQueries({ queryKey: ["chat-outbox", id], exact: true });
    cache.removeQueries({ queryKey: ["history", id], exact: true });
    cache.setQueryData<WebChat[]>(["web-chats"], (rows) =>
      rows?.filter((row) => row.id !== id),
    );
    await cache.invalidateQueries({ queryKey: ["records", "session"] });
  }

  return (
    <div className={"chat-workspace" + (drawerOpen ? " drawer-open" : "")}>
      <ChatSidebar
        groups={groups}
        selected={session}
        running={running}
        pending={pending}
        onSelect={select}
        onUpdate={updateChat}
        onDelete={removeChat}
        onSettings={onOpenSettings}
        onNew={() => {
          setDrawerOpen(false);
          const id = "web:" + Date.now();
          void act(async () => {
            await updateChat(id, {});
            onSelectSession(id);
          }, "");
        }}
      />
      <main className="chat-main">
        <header className="chat-header">
          <button
            type="button"
            aria-label={i18n.t("chat:workspace.sessions")}
            className="icon-button chat-menu"
            onClick={() => setDrawerOpen((open) => !open)}
          >
            <Menu size={18} aria-hidden="true" />
          </button>
          <h1>
            {titles.get(session)?.title ??
              (session.startsWith("web:")
                ? i18n.t("chat:workspace.newChat")
                : sessionTitle(session))}
          </h1>
        </header>
        <div
          className="chat-scroll"
          ref={scrollRef}
          onScroll={(event) => {
            const element = event.currentTarget;
            stickRef.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              80;
          }}
        >
          <div className="chat-stream" key={session} ref={streamRef}>
            {webChats.error && (
              <QueryErrorNotice
                message={i18n.t("chat:workspace.webChatsFailed", {
                  error: webChats.error.message,
                })}
              />
            )}
            {history.isPending && !outgoing.length && (
              <LoadingBar label={i18n.t("chat:workspace.loading")} />
            )}
            {history.error && (
              <QueryErrorNotice
                message={i18n.t("chat:workspace.historyFailed", {
                  error: history.error.message,
                })}
              />
            )}
            {empty && (
              <div className="chat-empty">
                <div className="chat-greeting">
                  <h2>
                    Hi I'm <BrandWordmark />
                  </h2>
                  <p>{i18n.t("chat:workspace.greeting")}</p>
                </div>
                {setup}
              </div>
            )}
            <AgentTimeline
              client={client}
              history={[...(history.data ?? []), ...outgoing]}
            />
            {current.map((row) => (
              <ApprovalCard
                key={row.id}
                approval={row}
                busy={busy}
                onResolve={(allow) => resolve(row.id, allow)}
              />
            ))}
            {waiting && (
              <div
                className="chat-loading"
                role="status"
                aria-label={i18n.t("chat:workspace.replying")}
              >
                <LoadingBar compact label={i18n.t("chat:workspace.waiting")} />
              </div>
            )}
          </div>
        </div>
        {session.startsWith("web:") ? (
          <ChatComposer
            key={session}
            onSend={(message, attachments) => {
              stickRef.current = true;
              send(message, attachments);
            }}
            client={client}
            act={act}
            session={session}
            assistantName={assistantName}
            running={running.has(session)}
          />
        ) : (
          <p className="muted chat-readonly">{i18n.t("chat:webOnly")}</p>
        )}
      </main>
    </div>
  );
}
