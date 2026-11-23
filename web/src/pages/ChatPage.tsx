import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { AgentTimeline } from "../components/AgentTimeline";
import { ChatComposer } from "../components/ChatComposer";
import { EmptyState } from "../components/EmptyState";
import { SessionOptions } from "../components/SessionPicker";
import { useChatOutbox } from "../hooks/useChatOutbox";
import type { PageProps } from "./common";
import i18n from "../i18n";

export function ChatPage({
  client,
  act,
  assistantName = "ShiritaiBot",
  session: selectedSession,
  onSelectSession,
}: PageProps & {
  assistantName?: string;
  session?: string;
  onSelectSession?: (id: string) => void;
}) {
  const [localSession, setLocalSession] = useState("web:owner");
  const session = selectedSession ?? localSession;
  const selectSession = onSelectSession ?? setLocalSession;
  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
    refetchInterval: 10000,
  });
  const history = useQuery({
    queryKey: ["history", session],
    queryFn: () => client.history(session),
    refetchInterval: 2500,
  });
  const { pending, send } = useChatOutbox(client, act, session, history.data);

  const options = [
    "web:owner",
    ...(sessions.data ?? []).map((row) => row.id),
    session,
  ];

  return (
    <>
      <div className="inline chat-toolbar">
        <select
          aria-label={i18n.t("chat:page.session")}
          value={session}
          onChange={(event) => selectSession(event.target.value)}
        >
          <SessionOptions ids={options} />
        </select>
        <button
          type="button"
          onClick={() => selectSession("web:" + Date.now())}
        >
          <Plus size={14} />
          {i18n.t("chat:page.new")}
        </button>
      </div>
      <section
        className="panel chat-panel"
        aria-label={i18n.t("chat:page.history")}
      >
        {history.error && (
          <QueryErrorNotice
            message={i18n.t("chat:page.historyFailed", {
              error: history.error.message,
            })}
          />
        )}
        {!history.data?.length && !pending.length && (
          <EmptyState text={i18n.t("chat:page.empty")} />
        )}
        <AgentTimeline
          client={client}
          history={[...(history.data ?? []), ...pending]}
        />
      </section>
      {session.startsWith("web:") ? (
        <ChatComposer
          key={session}
          onSend={send}
          client={client}
          act={act}
          session={session}
          assistantName={assistantName}
        />
      ) : (
        <p className="muted">{i18n.t("chat:webOnly")}</p>
      )}
    </>
  );
}
