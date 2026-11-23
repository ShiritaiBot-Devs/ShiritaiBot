import { lazy, Suspense, useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { HistoryMessage } from "../api/types";
import { buildTimeline } from "../lib/timeline";
import { ToolGroup } from "./ToolGroup";
import i18n from "../i18n";

const ChatMarkdown = lazy(() => import("./ChatMarkdown"));

function elapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}m ${seconds % 60}s` : `${seconds}s`;
}

function HelpMenuImage({
  client,
  id,
  text,
}: {
  client: ApiClient;
  id: string;
  text: string;
}) {
  const image = useQuery({
    queryKey: ["help-menu-image", id],
    queryFn: () => client.helpMenuImage(id),
    staleTime: Infinity,
    retry: false,
  });
  const [url, setUrl] = useState("");
  useEffect(() => {
    if (!image.data) return;
    const next = URL.createObjectURL(image.data);
    setUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [image.data]);
  return url ? (
    <img className="help-menu-image" src={url} alt={text} loading="lazy" />
  ) : (
    <div className="chat-plain">{text}</div>
  );
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
                {i18n.t("chat:timeline.elapsed", {
                  time: elapsed(msg._elapsed_seconds),
                })}
              </div>
            )}
            {msg._turn_status === "failed" && (
              <span className="turn-failed">
                {i18n.t("chat:timeline.failed")}
              </span>
            )}
            {msg._help_image ? (
              <HelpMenuImage
                key={msg._help_image}
                client={client}
                id={msg._help_image}
                text={text ?? ""}
              />
            ) : (
              <Suspense fallback={<div className="chat-plain">{text}</div>}>
                <ChatMarkdown client={client} text={text ?? ""} />
              </Suspense>
            )}
            {"sendFailed" in msg && msg.sendFailed === true && (
              <small role="status">{i18n.t("chat:timeline.sendFailed")}</small>
            )}
          </div>
        );
      })}
    </>
  );
}
