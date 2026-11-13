import { useCallback, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, type ApiClient } from "../api/client";
import type { ConfigSnapshot, Settings } from "../api/types";

export const CONFIG_SNAPSHOT_KEY = ["config", "snapshot"] as const;

/**
 * Holds an editable copy of the server configuration. Edits stay local until
 * save() is called; the query cache keeps the last saved version.
 */
export function useConfigDraft(client: ApiClient | null) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: CONFIG_SNAPSHOT_KEY,
    queryFn: () => client!.configSnapshot(),
    enabled: client !== null,
  });
  const [draft, setDraft] = useState<ConfigSnapshot | null>(null);
  const config = draft?.settings ?? query.data?.settings ?? null;

  const update = useCallback(
    (next: Settings) => {
      const saved =
        queryClient.getQueryData<ConfigSnapshot>(CONFIG_SNAPSHOT_KEY);
      setDraft((previous) =>
        JSON.stringify(next) === JSON.stringify(saved?.settings)
          ? null
          : {
              settings: next,
              revision: previous ? previous.revision : query.data?.revision,
            },
      );
    },
    [query.data?.revision, queryClient],
  );
  const discard = useCallback(() => setDraft(null), []);
  const save = useCallback(async () => {
    if (!client || !draft) return null;
    let result;
    try {
      result = await client.saveConfig(draft.settings, draft.revision);
    } catch (error) {
      if (error instanceof ApiError && error.status === 412) {
        await queryClient.cancelQueries({ queryKey: ["config"] });
        await queryClient.invalidateQueries({ queryKey: ["config"] });
      }
      throw error;
    }
    // Refresh the server copy before dropping the draft so the form never
    // flashes the previous values for one frame.
    const keys = [["config"], ["tools"], ["diagnostics"]];
    await Promise.all(
      keys.map((queryKey) => queryClient.cancelQueries({ queryKey })),
    );
    await Promise.all(
      keys.map((queryKey) => queryClient.invalidateQueries({ queryKey })),
    );
    setDraft((current) => (current === draft ? null : current));
    return result;
  }, [client, draft, queryClient]);

  return {
    config,
    saved: query.data,
    loading: query.isPending,
    dirty: draft !== null,
    error: query.error instanceof Error ? query.error : null,
    update,
    discard,
    save,
  };
}
