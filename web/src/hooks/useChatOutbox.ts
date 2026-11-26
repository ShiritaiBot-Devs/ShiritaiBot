import { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { ChatAttachment, HistoryMessage } from "../api/types";
import type { Action } from "../pages/common";

type Outgoing = HistoryMessage & { localId: string; sendFailed?: boolean };

export function useChatOutbox(
  client: ApiClient,
  act: Action,
  session: string,
  history?: HistoryMessage[],
) {
  const cache = useQueryClient();
  const key = ["chat-outbox", session];
  const { data: outgoing } = useQuery<Outgoing[]>({
    queryKey: key,
    initialData: [],
    enabled: false,
  });
  const received = new Set(history?.flatMap((row) => row._client_ids ?? []));
  const pending = outgoing.filter((row) => !received.has(row.localId));

  useEffect(() => {
    if (!history?.length) return;
    const ids = new Set(history.flatMap((row) => row._client_ids ?? []));
    cache.setQueryData<Outgoing[]>(["chat-outbox", session], (rows) =>
      rows?.some((row) => ids.has(row.localId))
        ? rows.filter((row) => !ids.has(row.localId))
        : rows,
    );
  }, [history, session, cache]);

  function send(text: string, attachments: ChatAttachment[] = []) {
    // Settings commands use the alert path instead of adding an optimistic bubble.
    if (
      /^\/(?:model|persona|lang|tools|permissions|mode)\s+\S/i.test(text) ||
      /^\/grants\s+(?:revoke|撤销)/i.test(text)
    ) {
      void act(
        () =>
          client.sendMessage(
            text,
            session,
            undefined,
            attachments.map((file) => file.id),
          ),
        "",
      );
      return;
    }
    const id = Array.from(crypto.getRandomValues(new Uint32Array(4)), (x) =>
      x.toString(16).padStart(8, "0"),
    ).join("");
    const before = cache.getQueryData<Outgoing[]>(key) ?? [];
    cache.setQueryData<Outgoing[]>(key, (rows) => [
      ...(rows ?? []),
      {
        role: "user",
        content: [text, ...attachments.map((file) => file.name)]
          .filter(Boolean)
          .join("\n"),
        localId: id,
        _client_ids: [id],
      },
    ]);
    let status = "";
    void act(async () => {
      try {
        status = (
          await client.sendMessage(
            text,
            session,
            id,
            attachments.map((file) => file.id),
          )
        ).status;
      } catch (error) {
        cache.setQueryData<Outgoing[]>(key, (rows) =>
          rows?.map((row) =>
            row.localId === id ? { ...row, sendFailed: true } : row,
          ),
        );
        throw error;
      }
    }, "").then(() => {
      // Plugins can consume a message without writing it to model history.
      const discard = new Set<string>();
      if (
        ["plugin", "ignored", "cancelled", "settings_command"].includes(status)
      )
        discard.add(id);
      if (
        status === "cancelled" ||
        (status === "command" &&
          /^\/(stop|cancel|new|clear|reset)(?:\s|$)/i.test(text))
      ) {
        for (const row of before) if (!row.sendFailed) discard.add(row.localId);
      }
      if (discard.size)
        cache.setQueryData<Outgoing[]>(key, (rows) =>
          rows?.filter((row) => !discard.has(row.localId)),
        );
    });
  }
  return { pending, send };
}
