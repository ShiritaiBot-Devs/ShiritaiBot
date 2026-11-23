import { LoadingBar } from "../components/LoadingBar";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useQuery } from "@tanstack/react-query";
import { Details } from "../components/Details";
import { EmptyState } from "../components/EmptyState";
import { useState } from "react";
import { Pagination, PAGE_SIZE } from "../components/Pagination";
import type { PageProps } from "./common";
import i18n from "../i18n";

export function EventsPage({ client }: PageProps) {
  const [page, setPage] = useState(1);
  const events = useQuery({
    queryKey: ["records", "event", page],
    queryFn: () => client.recordsPage("event", page, PAGE_SIZE),
    refetchInterval: 4000,
  });

  return (
    <section className="panel">
      <h2>{i18n.t("events:timeline")}</h2>
      {events.error && (
        <QueryErrorNotice
          message={i18n.t("events:loadFailed", { error: events.error.message })}
        />
      )}
      {events.data?.items.map((row) => (
        <article className="record" key={row.id}>
          <div className="panel-head">
            <strong>{row.event.title}</strong>
            <span className="tag">{row.decision?.decision ?? row.status}</span>
          </div>
          <p>{row.decision?.reason}</p>
          <small>
            {row.event.source} · {row.event.kind}
          </small>
          <Details value={row} />
        </article>
      ))}
      {!events.data?.items.length && !events.error && !events.isPending && (
        <EmptyState text={i18n.t("events:empty")} />
      )}
      {events.isPending && <LoadingBar label={i18n.t("events:loading")} />}
      <Pagination
        label={i18n.t("events:pages")}
        page={events.data?.page ?? page}
        total={events.data?.total ?? 0}
        onChange={setPage}
        loading={events.isFetching}
      />
    </section>
  );
}
