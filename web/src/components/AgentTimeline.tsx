import { lazy, Suspense } from "react";
import type { ApiClient } from "../api/client";
import type { HistoryMessage } from "../api/types";
import { buildTimeline } from "../lib/timeline";
import { ToolGroup } from "./ToolGroup";

const ChatMarkdown = lazy(() => import("./ChatMarkdown"));

function elapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
}

export function AgentTimeline({
  history,
  client,
}: {
  history: HistoryMessage[];
  client: ApiClient;
}) {
  return (
    <>
      {buildTimeline(history).map((entry, index) => {
        if (entry.kind === "tools")
          return (
            <ToolGroup
              key={`tools-${entry.seq ?? index}`}
              items={entry.items}
            />
          );
        const msg = entry.message;
        const user = entry.kind === "user";
        const text = user
          ? (msg._original_content ?? msg.content?.split("\n<context")[0])
          : msg.content;
        const local = "localId" in msg;
        return (
          <div
            key={msg._client_ids?.[0] ?? msg._seq ?? index}
            className={`message ${entry.kind}${local ? " outgoing" : ""}`}
          >
            {!user && msg._elapsed_seconds !== undefined && (
              <div className="turn-elapsed">
                用时 {elapsed(msg._elapsed_seconds)}
              </div>
            )}
            {msg._turn_status === "failed" && (
              <span className="turn-failed">本轮失败</span>
            )}
            <Suspense fallback={<div className="chat-plain">{text}</div>}>
              <ChatMarkdown client={client} text={text ?? ""} />
            </Suspense>
            {"sendFailed" in msg && msg.sendFailed === true && (
              <small role="status">发送失败</small>
            )}
          </div>
        );
      })}
    </>
  );
}
