import { useId } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { AdapterConfig } from "../api/types";
import { adapterSessionId, isMainSession, sessionTitle } from "../lib/sessions";

export function SessionPicker({
  client,
  adapters,
  suggestions,
  session,
  onChange,
}: {
  client: ApiClient;
  adapters: AdapterConfig[];
  suggestions: string[];
  session: string;
  onChange: (session: string) => void;
}) {
  const listId = useId();
  const observed = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
  });
  const configured = adapters.flatMap((adapter) => [
    ...[adapter.owner_id, ...adapter.friends]
      .filter(Boolean)
      .map((target) => adapterSessionId(adapter, "private", target)),
    ...adapter.groups.map((target) =>
      adapterSessionId(adapter, "group", target),
    ),
  ]);
  const sessions = [
    ...new Set([
      "web:owner",
      ...suggestions,
      ...configured,
      ...(observed.data ?? []).map((row) => row.id),
    ]),
  ]
    .filter(isMainSession)
    .sort();

  return (
    <label className="field">
      <span className="field-name">会话</span>
      <input
        list={listId}
        placeholder="选择或输入完整会话 ID"
        value={session}
        onChange={(event) => onChange(event.target.value.trim())}
      />
      <datalist id={listId}>
        {sessions.map((value) => (
          <option key={value} value={value}>
            {sessionTitle(value)}
          </option>
        ))}
      </datalist>
    </label>
  );
}
