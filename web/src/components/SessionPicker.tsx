import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import type { ApiClient } from "../api/client";
import type { AdapterConfig } from "../api/types";
import {
  adapterSessionId,
  isMainSession,
  sessionGroups,
} from "../lib/sessions";
import i18n from "../i18n";

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
  onReset,
  titles,
}: {
  client: ApiClient;
  adapters: AdapterConfig[];
  suggestions: string[];
  session: string;
  onChange: (session: string) => void;
  onReset?: () => void;
  titles?: ReadonlyMap<string, string>;
}) {
  const id = useId();
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
    ...(titles?.keys() ?? []),
    ...configured,
    ...(observed.data ?? [])
      .filter((row) => !row.background)
      .map((row) => row.id),
  ].filter(isMainSession);

  return (
    <div className="field">
      <label className="field-name" htmlFor={id}>
        {i18n.t("common:sessionPicker.session")}
      </label>
      <div className="inline">
        <select
          id={id}
          value={session}
          onChange={(event) => onChange(event.target.value)}
        >
          <SessionOptions ids={sessions} titles={titles} />
        </select>
        {onReset && (
          <button type="button" onClick={onReset}>
            {i18n.t("common:sessionPicker.reset")}
          </button>
        )}
      </div>
    </div>
  );
}
