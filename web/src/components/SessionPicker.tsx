import { useQuery } from "@tanstack/react-query";
import type { ApiClient } from "../api/client";
import type { AdapterConfig } from "../api/types";
import {
  adapterSessionId,
  isMainSession,
  sessionGroups,
} from "../lib/sessions";

export function SessionOptions({
  ids,
  titles,
}: {
  ids: string[];
  titles?: ReadonlyMap<string, string>;
}) {
  return sessionGroups([...new Set(ids)], {}).map((group) => (
    <optgroup key={group.key} label={group.label}>
      {group.items.map(({ id, title }) => (
        <option key={id} value={id} title={id}>
          {titles?.get(id) ?? title}
        </option>
      ))}
    </optgroup>
  ));
}

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
    "web:owner",
    session,
    ...suggestions,
    ...configured,
    ...(observed.data ?? [])
      .filter((row) => !row.background)
      .map((row) => row.id),
  ].filter(isMainSession);

  return (
    <label className="field">
      <span className="field-name">会话</span>
      <select
        aria-label="会话"
        value={session}
        onChange={(event) => onChange(event.target.value)}
      >
        <SessionOptions ids={sessions} />
      </select>
    </label>
  );
}
