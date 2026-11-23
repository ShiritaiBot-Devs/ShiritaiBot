import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Plus, X } from "lucide-react";
import { EmptyState } from "../components/EmptyState";
import { SessionOptions } from "../components/SessionPicker";
import type { RoutineKind, RoutineRecord } from "../api/types";
import { isMainSession, sessionTitle } from "../lib/sessions";
import {
  dateFormat,
  formatDateTime,
  localInputToIso,
  ownerTimeZone,
} from "../lib/utils";
import type { PageProps } from "./common";
import i18n, { currentLocale, tx } from "../i18n";

const KINDS: { value: RoutineKind; label: string }[] = [
  { value: "todo", label: "routine:kinds.todo" },
  { value: "event", label: "routine:kinds.event" },
  { value: "course", label: "routine:kinds.course" },
  { value: "reminder", label: "routine:kinds.reminder" },
  { value: "job", label: "routine:kinds.job" },
];

const KIND_LABEL: Record<RoutineKind, string> = {
  todo: "routine:kinds.todo",
  event: "routine:kinds.event",
  course: "routine:kinds.course",
  reminder: "routine:kinds.reminder",
  job: "routine:kindShort.job",
};

const STATUS_LABEL: Record<string, string> = {
  open: "routine:status.open",
  scheduled: "routine:status.scheduled",
  fired: "routine:status.fired",
  completed: "routine:status.completed",
  cancelled: "routine:status.cancelled",
};

/** Monday-first weekday names in the interface language. */
function weekdayNames(width: "narrow" | "short") {
  const format = new Intl.DateTimeFormat(currentLocale(), {
    weekday: width,
    timeZone: "UTC",
  });
  // 1 January 2024 was a Monday.
  return Array.from({ length: 7 }, (_, i) =>
    format.format(Date.UTC(2024, 0, 1 + i)),
  );
}
const DEFAULT_SESSION = "web:owner";
const LEGACY_SCOPE = "owner";
/** Guard against runaway multi-day events. */
const MAX_SPAN_DAYS = 62;

const dayKeyFormat = () =>
  dateFormat({ year: "numeric", month: "2-digit", day: "2-digit" }, "en-CA");
const clockFormat = () =>
  dateFormat({ hour: "2-digit", minute: "2-digit", hour12: false });

/** "YYYY-MM-DD" of the instant in the dashboard time zone. */
function dayKey(value: string | Date): string | null {
  const date = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(date.getTime()) ? null : dayKeyFormat().format(date);
}

function clock(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : clockFormat().format(date);
}

/** Calendar arithmetic on date-only keys, done in UTC so DST never bites. */
function keyToUtc(key: string): Date {
  const [y, m, d] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
}

function utcToKey(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addDays(key: string, days: number): string {
  const date = keyToUtc(key);
  date.setUTCDate(date.getUTCDate() + days);
  return utcToKey(date);
}

function isDone(row: RoutineRecord): boolean {
  return row.status === "completed" || row.status === "cancelled";
}

/** Every day key a routine occupies (events with an end span several days). */
function routineDays(row: RoutineRecord): string[] {
  if (!row.due) return [];
  const start = dayKey(row.due);
  if (!start) return [];
  const end = row.end ? dayKey(row.end) : null;
  const days = [start];
  if (end && end > start) {
    let cursor = start;
    while (cursor < end && days.length < MAX_SPAN_DAYS) {
      cursor = addDays(cursor, 1);
      days.push(cursor);
    }
  }
  return days;
}

/** 6×7 grid of day keys covering the month, weeks starting on Monday. */
function monthGrid(year: number, month: number): string[] {
  const first = new Date(Date.UTC(year, month, 1));
  const offset = (first.getUTCDay() + 6) % 7;
  const start = addDays(utcToKey(first), -offset);
  return Array.from({ length: 42 }, (_, index) => addDays(start, index));
}

function sortByDue(a: RoutineRecord, b: RoutineRecord): number {
  return (a.due ?? "").localeCompare(b.due ?? "");
}

function RoutineItem({
  row,
  names,
  client,
  act,
}: { row: RoutineRecord; names: ReadonlyMap<string, string> } & Pick<
  PageProps,
  "client" | "act"
>) {
  const timeText = row.due
    ? row.end
      ? `${formatDateTime(row.due)} – ${formatDateTime(row.end)}`
      : formatDateTime(row.due)
    : row.cron
      ? row.next_fire
        ? i18n.t("routine:cronNext", {
            cron: row.cron,
            next: formatDateTime(row.next_fire),
          })
        : i18n.t("routine:cron", { cron: row.cron })
      : i18n.t("routine:noDue");
  return (
    <article
      className={`routine-item kind-${row.kind}${isDone(row) ? " done" : ""}`}
    >
      <div className="routine-item-main">
        <strong>{row.title}</strong>
        <span className="muted" title={row.scope}>
          {names.get(row.scope) ?? sessionTitle(row.scope)} · {timeText}
        </span>
      </div>
      <span className="tag">
        {tx(KIND_LABEL[row.kind] ?? row.kind)} ·{" "}
        {tx(STATUS_LABEL[row.status] ?? row.status)}
      </span>
      {!isDone(row) && (
        <div className="actions">
          <button
            type="button"
            onClick={() =>
              void act(
                () => client.setRoutineStatus(row.id, "completed"),
                i18n.t("routine:markedDone"),
              )
            }
          >
            {i18n.t("routine:done")}
          </button>
          <button
            type="button"
            onClick={() =>
              void act(
                () => client.setRoutineStatus(row.id, "cancelled"),
                i18n.t("routine:cancelled"),
              )
            }
          >
            {i18n.t("routine:cancel")}
          </button>
        </div>
      )}
    </article>
  );
}

function RoutineCalendar({
  rows,
  names,
  client,
  act,
}: { rows: RoutineRecord[]; names: ReadonlyMap<string, string> } & Pick<
  PageProps,
  "client" | "act"
>) {
  const today = dayKey(new Date()) ?? utcToKey(new Date());
  const [cursor, setCursor] = useState(() => {
    const [y, m] = today.split("-").map(Number);
    return { year: y, month: m - 1 };
  });
  const [selection, setSelection] = useState<{
    day: string;
    anchor: HTMLButtonElement;
  } | null>(null);

  const byDay = useMemo(() => {
    const map = new Map<string, RoutineRecord[]>();
    for (const row of rows) {
      for (const key of routineDays(row)) {
        const list = map.get(key);
        if (list) list.push(row);
        else map.set(key, [row]);
      }
    }
    for (const list of map.values()) list.sort(sortByDue);
    return map;
  }, [rows]);

  const undated = useMemo(
    () =>
      rows
        .filter((row) => !row.due)
        .sort((a, b) => Number(isDone(a)) - Number(isDone(b))),
    [rows],
  );

  const grid = monthGrid(cursor.year, cursor.month);
  const monthPrefix = `${cursor.year}-${String(cursor.month + 1).padStart(2, "0")}`;
  function shiftMonth(delta: number) {
    setSelection(null);
    setCursor(({ year, month }) => {
      const next = new Date(Date.UTC(year, month + delta, 1));
      return { year: next.getUTCFullYear(), month: next.getUTCMonth() };
    });
  }

  function goToday() {
    const [y, m] = today.split("-").map(Number);
    setCursor({ year: y, month: m - 1 });
    setSelection(null);
  }

  return (
    <>
      <section className="panel">
        <div className="panel-head calendar-head">
          <h2>
            {new Intl.DateTimeFormat(currentLocale(), {
              year: "numeric",
              month: "long",
              timeZone: "UTC",
            }).format(Date.UTC(cursor.year, cursor.month, 1))}
          </h2>
          <div className="inline calendar-nav">
            <button
              type="button"
              aria-label={i18n.t("routine:prevMonth")}
              onClick={() => shiftMonth(-1)}
            >
              <ChevronLeft size={16} />
            </button>
            <button type="button" onClick={goToday}>
              {i18n.t("routine:today")}
            </button>
            <button
              type="button"
              aria-label={i18n.t("routine:nextMonth")}
              onClick={() => shiftMonth(1)}
            >
              <ChevronRight size={16} />
            </button>
          </div>
        </div>
        <div
          className="calendar"
          role="grid"
          aria-label={i18n.t("routine:calendar")}
        >
          {weekdayNames(currentLocale() === "zh-CN" ? "narrow" : "short").map(
            (name) => (
              <div className="calendar-weekday" role="columnheader" key={name}>
                {name}
              </div>
            ),
          )}
          {grid.map((key) => {
            const items = (byDay.get(key) ?? []).filter(
              (row) => row.kind !== "course",
            );
            const classes = [
              "calendar-day",
              key.startsWith(monthPrefix) ? "" : "outside",
              key === today ? "today" : "",
              key === selection?.day ? "selected" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <button
                type="button"
                role="gridcell"
                key={key}
                className={classes}
                aria-pressed={key === selection?.day}
                aria-haspopup="dialog"
                aria-expanded={key === selection?.day}
                aria-label={i18n.t("routine:dayLabel", {
                  day: key,
                  count: items.length,
                })}
                onClick={(event) => {
                  setSelection(
                    selection?.day === key
                      ? null
                      : { day: key, anchor: event.currentTarget },
                  );
                }}
              >
                <span className="calendar-date">{Number(key.slice(8))}</span>
                <span className="calendar-items">
                  {items.slice(0, 3).map((row) => (
                    <span
                      key={row.id}
                      className={`calendar-chip kind-${row.kind}${isDone(row) ? " done" : ""}`}
                      title={`${names.get(row.scope) ?? sessionTitle(row.scope)} · ${row.title}`}
                    >
                      {row.due && dayKey(row.due) === key && (
                        <span className="calendar-chip-time">
                          {clock(row.due)}
                        </span>
                      )}
                      {row.title}
                    </span>
                  ))}
                  {items.length > 3 && (
                    <span className="calendar-more">+{items.length - 3}</span>
                  )}
                </span>
                {items.length > 0 && (
                  <span className="calendar-dots" aria-hidden="true">
                    {items.slice(0, 4).map((row) => (
                      <i
                        key={row.id}
                        className={`kind-${row.kind}${isDone(row) ? " done" : ""}`}
                      />
                    ))}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="calendar-legend">
          {(["event", "reminder", "todo", "job"] as RoutineKind[]).map(
            (kind) => (
              <span key={kind}>
                <i className={`kind-${kind}`} />
                {tx(KIND_LABEL[kind])}
              </span>
            ),
          )}
          <span>{i18n.t("routine:calendarHint")}</span>
        </div>
      </section>
      {selection && (
        <RoutineDayPopover
          key={selection.day}
          day={selection.day}
          anchor={selection.anchor}
          rows={byDay.get(selection.day) ?? []}
          onClose={() => setSelection(null)}
          names={names}
          client={client}
          act={act}
        />
      )}
      {undated.length > 0 && (
        <section className="panel">
          <h2>{i18n.t("routine:unscheduled")}</h2>
          {undated.map((row) => (
            <RoutineItem
              key={row.id}
              row={row}
              names={names}
              client={client}
              act={act}
            />
          ))}
        </section>
      )}
    </>
  );
}

function RoutineDayPopover({
  day,
  anchor,
  rows,
  onClose,
  names,
  client,
  act,
}: {
  day: string;
  anchor: HTMLButtonElement;
  rows: RoutineRecord[];
  onClose: () => void;
  names: ReadonlyMap<string, string>;
} & Pick<PageProps, "client" | "act">) {
  const popover = useRef<HTMLDivElement>(null);
  const titleId = useId();
  // Event handlers always use the latest callback without resetting focus on refetch.
  const close = useRef(onClose);
  close.current = onClose;

  useLayoutEffect(() => {
    const node = popover.current;
    if (!node) return;

    function position() {
      if (!node) return;
      const rect = anchor.getBoundingClientRect();
      const gap = 12;
      const width = node.offsetWidth;
      const height = node.offsetHeight;
      const viewportWidth = document.documentElement.clientWidth;
      const viewportHeight = window.innerHeight;
      let left = rect.right + gap;
      let top = rect.top;
      if (left + width > viewportWidth - gap) {
        if (rect.left - gap - width >= gap) {
          left = rect.left - gap - width;
        } else {
          left = rect.left;
          top = rect.bottom + gap;
          if (top + height > viewportHeight - gap) {
            top = rect.top - height - gap;
          }
        }
      }
      node.style.left = `${Math.max(gap, Math.min(left, viewportWidth - width - gap))}px`;
      node.style.top = `${Math.max(gap, Math.min(top, viewportHeight - height - gap))}px`;
    }

    function dismiss(event: PointerEvent) {
      if (
        event.target instanceof Node &&
        !node?.contains(event.target) &&
        !anchor.contains(event.target)
      ) {
        close.current();
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        anchor.focus();
        close.current();
      }
    }

    position();
    node.focus({ preventScroll: true });
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(position);
    observer?.observe(node);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [anchor]);

  const title = new Intl.DateTimeFormat(currentLocale(), {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
    timeZone: "UTC",
  }).format(keyToUtc(day));
  return createPortal(
    <div
      className="routine-day-popover"
      ref={popover}
      role="dialog"
      aria-labelledby={titleId}
      tabIndex={-1}
    >
      <div className="panel-head">
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          aria-label={i18n.t("routine:closeDay")}
          onClick={() => {
            anchor.focus();
            onClose();
          }}
        >
          <X size={16} />
        </button>
      </div>
      {rows.length ? (
        rows.map((row) => (
          <RoutineItem
            key={row.id}
            row={row}
            names={names}
            client={client}
            act={act}
          />
        ))
      ) : (
        <EmptyState text={i18n.t("routine:dayEmpty")} />
      )}
    </div>,
    document.body,
  );
}

export function RoutinePage({ client, act }: PageProps) {
  const [filter, setFilter] = useState("");
  const [session, setSession] = useState(DEFAULT_SESSION);
  const [kind, setKind] = useState<RoutineKind>("todo");
  const [title, setTitle] = useState("");
  const [due, setDue] = useState("");
  const [cron, setCron] = useState("30 7 * * *");
  const routines = useQuery({
    queryKey: ["records", "routine"],
    queryFn: () => client.routines(),
    refetchInterval: 4000,
  });
  const sessions = useQuery({
    queryKey: ["records", "session"],
    queryFn: () => client.records("session"),
    refetchInterval: 10000,
  });
  const chats = useQuery({
    queryKey: ["web-chats"],
    queryFn: () => client.webChats(),
  });
  const names = new Map(chats.data?.map((chat) => [chat.id, chat.title]));
  names.set(LEGACY_SCOPE, i18n.t("routine:legacy"));

  const rows = useMemo(
    () =>
      (routines.data ?? []).filter((row) => !filter || row.scope === filter),
    [routines.data, filter],
  );
  const routineScopes = (routines.data ?? []).map((row) => row.scope);
  const destinations = [
    DEFAULT_SESSION,
    session,
    ...(sessions.data ?? []).map((row) => row.id),
    ...(chats.data ?? []).map((chat) => chat.id),
    ...routineScopes,
  ].filter(isMainSession);
  const filterScopes = [...destinations, ...routineScopes, filter].filter(
    (scope) => scope && scope !== LEGACY_SCOPE,
  );

  function create(event: React.FormEvent) {
    event.preventDefault();
    void act(async () => {
      await client.createRoutine({
        session,
        kind,
        title,
        due: localInputToIso(due),
        cron: kind === "job" ? cron : "",
        timezone: ownerTimeZone(),
      });
      setTitle("");
      setDue("");
      setFilter((current) =>
        current && current === filter ? session : current,
      );
    }, i18n.t("routine:created"));
  }

  return (
    <>
      <section className="panel">
        <label className="inline">
          <span className="field-name">{i18n.t("routine:viewSession")}</span>
          <select
            value={filter}
            onChange={(event) => {
              const scope = event.target.value;
              setFilter(scope);
              if (isMainSession(scope)) setSession(scope);
            }}
          >
            <option value="">{i18n.t("routine:allSessions")}</option>
            {(filter === LEGACY_SCOPE ||
              routineScopes.includes(LEGACY_SCOPE)) && (
              <option value={LEGACY_SCOPE}>{names.get(LEGACY_SCOPE)}</option>
            )}
            <SessionOptions ids={filterScopes} titles={names} />
          </select>
        </label>
      </section>
      <section className="panel">
        <h2>{i18n.t("routine:new.title")}</h2>
        <form onSubmit={create}>
          <div className="field-grid">
            <label className="field">
              <span className="field-name">
                {i18n.t("routine:new.session")}
              </span>
              <select
                value={session}
                onChange={(event) => setSession(event.target.value)}
              >
                <SessionOptions ids={destinations} titles={names} />
              </select>
            </label>
            <div className="field">
              <span className="field-name">{i18n.t("routine:new.kind")}</span>
              <select
                aria-label={i18n.t("routine:new.kind")}
                value={kind}
                onChange={(event) => setKind(event.target.value as RoutineKind)}
              >
                {KINDS.map((item) => (
                  <option key={item.value} value={item.value}>
                    {tx(item.label)}
                  </option>
                ))}
              </select>
            </div>
            <div className="field">
              <span className="field-name">
                {i18n.t("routine:new.content")}
              </span>
              <input
                aria-label={i18n.t("routine:new.content")}
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                required
              />
            </div>
            {kind === "job" ? (
              <div className="field">
                <span className="field-name">
                  {i18n.t("withDetail", {
                    label: "Cron",
                    detail: ownerTimeZone(),
                  })}
                </span>
                <input
                  aria-label="Cron"
                  value={cron}
                  onChange={(event) => setCron(event.target.value)}
                />
              </div>
            ) : (
              <div className="field">
                <span className="field-name">{i18n.t("routine:new.time")}</span>
                <input
                  aria-label={i18n.t("routine:new.timeLabel")}
                  type="datetime-local"
                  value={due}
                  onChange={(event) => setDue(event.target.value)}
                  required={
                    kind === "event" || kind === "course" || kind === "reminder"
                  }
                />
                {kind === "course" && (
                  <span className="muted">{i18n.t("routine:courseHint")}</span>
                )}
              </div>
            )}
          </div>
          <button type="submit" className="primary">
            <Plus size={16} />
            {i18n.t("routine:create")}
          </button>
        </form>
      </section>
      {routines.error && (
        <section className="panel">
          <QueryErrorNotice
            message={i18n.t("routine:loadFailed", {
              error: routines.error.message,
            })}
          />
        </section>
      )}
      <RoutineCalendar rows={rows} names={names} client={client} act={act} />
      {filter && routines.isSuccess && rows.length === 0 && (
        <EmptyState text={i18n.t("routine:empty")} />
      )}
    </>
  );
}
