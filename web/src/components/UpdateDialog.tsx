import { LoadingBar } from "./LoadingBar";
import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Trans, useTranslation } from "react-i18next";
import {
  CircleCheck,
  CircleX,
  ExternalLink,
  GitCommitHorizontal,
  RefreshCw,
  Tag,
  X,
} from "lucide-react";
import type { ApiClient } from "../api/client";
import type { UpdateInspect, UpdatePhase } from "../api/types";
import { Markdown } from "./Markdown";
import { Pagination } from "./Pagination";
import {
  formatDate,
  isCurrentRelease,
  isNewer,
  shortSha,
} from "../lib/version";

const PAGE_SIZE = 6;
const ACTIVE: UpdatePhase[] = [
  "downloading",
  "building",
  "deploying",
  "restarting",
];
const STEPS = ["downloading", "building", "deploying", "restarting"] as const;

type Selection = { kind: "release"; tag: string } | { kind: "commit" };

export function UpdateDialog({
  client,
  onClose,
  onRestarting,
  restarting,
  blockedReason,
}: {
  client: ApiClient;
  onClose: () => void;
  onRestarting: () => void;
  restarting: boolean;
  blockedReason: string | null;
}) {
  const { t } = useTranslation(["update", "common"]);
  const queryClient = useQueryClient();
  const [closing, setClosing] = useState(false);
  const [showBeta, setShowBeta] = useState(false);
  const [showCommit, setShowCommit] = useState(false);
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Selection | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [inspecting, setInspecting] = useState(false);
  const [inspection, setInspection] = useState<UpdateInspect | null>(null);
  const [inspectError, setInspectError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [finished, setFinished] = useState(false);
  const [dismissedFailure, setDismissedFailure] = useState(false);
  const handedOff = useRef(false);
  const dialog = useRef<HTMLDivElement>(null);

  const status = useQuery({
    queryKey: ["update", "status"],
    queryFn: () => client.updateStatus(),
    refetchInterval: (query) =>
      restarting ||
      starting ||
      ACTIVE.includes(query.state.data?.phase ?? "idle")
        ? 1000
        : false,
    retry: false,
  });
  const releases = useQuery({
    queryKey: ["update", "releases", page, showBeta],
    queryFn: () => client.updateReleases(page, PAGE_SIZE, showBeta),
    placeholderData: (previous) => previous,
    enabled: status.isSuccess && status.data?.mode !== "git",
  });
  const preview = useQuery({
    queryKey: ["update", "preview"],
    queryFn: () => client.updatePreview(),
    enabled: showCommit || status.data?.mode === "git",
  });

  const phase = status.data?.phase ?? "idle";
  const active = starting || ACTIVE.includes(phase);
  const current = status.data?.current;
  const gitMode = status.data?.mode === "git";
  const includeCommit = showCommit || gitMode;
  const items = gitMode ? [] : (releases.data?.items ?? []);
  const commit = includeCommit ? (preview.data?.commit ?? null) : null;

  // Hand the wait for the new process to the console's restart tracker exactly once.
  useEffect(() => {
    if (phase !== "restarting" || handedOff.current) return;
    handedOff.current = true;
    onRestarting();
  }, [phase, onRestarting]);

  useEffect(() => {
    if (!handedOff.current || restarting) return;
    handedOff.current = false;
    setFinished(true);
    void queryClient.invalidateQueries({ queryKey: ["update"] });
  }, [restarting, queryClient]);

  useEffect(() => {
    dialog.current?.focus();
  }, []);

  // Keep the selection valid as pages and filters change.
  useEffect(() => {
    if (
      selected?.kind === "commit" &&
      includeCommit &&
      (commit || preview.isPending)
    )
      return;
    if (
      selected?.kind === "release" &&
      items.some((r) => r.tag === selected.tag)
    )
      return;
    if (commit && page === 1) setSelected({ kind: "commit" });
    else if (items[0]) setSelected({ kind: "release", tag: items[0].tag });
    else setSelected(null);
    setConfirming(false);
    setInspection(null);
    setInspectError(null);
  }, [items, commit, selected, page, includeCommit, preview.isPending]);

  function close() {
    if (active) return;
    setClosing(true);
    setTimeout(onClose, 180);
  }

  async function refresh() {
    setRefreshing(true);
    try {
      if (gitMode) {
        const latest = await client.updatePreview(true);
        queryClient.setQueryData(["update", "preview"], latest);
        await status.refetch();
        return;
      }
      const [listing] = await Promise.all([
        client.updateReleases(1, PAGE_SIZE, showBeta, true),
        showCommit ? client.updatePreview(true) : Promise.resolve(null),
      ]);
      setPage(1);
      queryClient.setQueryData(["update", "releases", 1, showBeta], listing);
      await queryClient.invalidateQueries({ queryKey: ["update"] });
    } catch {
      // Query errors are shown inline after invalidation.
    } finally {
      setRefreshing(false);
    }
  }

  async function beginConfirm() {
    if (!target) return;
    setConfirming(true);
    setInspection(null);
    setInspectError(null);
    setInspecting(true);
    try {
      setInspection(await client.inspectUpdate(target.kind, target.ref));
    } catch (error) {
      setInspectError(error instanceof Error ? error.message : String(error));
    } finally {
      setInspecting(false);
    }
  }

  async function apply() {
    if (!target) return;
    setStarting(true);
    setStartError(null);
    setFinished(false);
    setDismissedFailure(false);
    try {
      const next = await client.applyUpdate(target.kind, target.ref);
      queryClient.setQueryData(["update", "status"], next);
    } catch (error) {
      setStartError(error instanceof Error ? error.message : String(error));
    } finally {
      setStarting(false);
      setConfirming(false);
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === "Escape") {
      event.stopPropagation();
      close();
    }
  }

  const release =
    selected?.kind === "release"
      ? items.find((r) => r.tag === selected.tag)
      : undefined;
  const target =
    selected?.kind === "commit" && commit
      ? {
          kind: "commit" as const,
          ref: commit.sha,
          label: shortSha(commit.sha),
        }
      : release
        ? { kind: "release" as const, ref: release.tag, label: release.tag }
        : null;
  const selectedIsCurrent = Boolean(
    current &&
    ((release && isCurrentRelease(release, current)) ||
      (selected?.kind === "commit" &&
        commit &&
        current.kind === "commit" &&
        current.ref === commit.sha)),
  );
  const unavailable = status.data && !status.data.available;
  const applyBlocked =
    blockedReason ?? (unavailable ? status.data?.reason : null) ?? null;
  const showProgress =
    active || (phase === "failed" && !dismissedFailure) || finished;

  return (
    <div
      className={"update-backdrop" + (closing ? " closing" : "")}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) close();
      }}
    >
      <div
        ref={dialog}
        role="dialog"
        aria-modal="true"
        aria-labelledby="update-title"
        className="update-dialog"
        tabIndex={-1}
        onKeyDown={onKeyDown}
      >
        <div className="update-head">
          <div>
            <h2 id="update-title">{t("title")}</h2>
            <small className="muted">
              {t("currentVersion")}{" "}
              <code>
                {current
                  ? current.kind === "commit"
                    ? shortSha(current.ref)
                    : (current.ref ?? "v" + current.version)
                  : "…"}
              </code>
              {current?.deployed_at &&
                t("deployedAt", { date: formatDate(current.deployed_at) })}
              {status.data && (
                <>
                  {" · "}
                  <a
                    href={"https://github.com/" + status.data.repository}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    {status.data.repository}
                  </a>
                </>
              )}
            </small>
          </div>
          <div className="actions">
            {!showProgress && (
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={refreshing}
                aria-label={t("recheck")}
                title={t("recheck")}
              >
                {refreshing ? (
                  <LoadingBar compact label={t("title")} />
                ) : (
                  <RefreshCw size={14} aria-hidden="true" />
                )}
              </button>
            )}
            <button
              type="button"
              onClick={close}
              disabled={active}
              aria-label={t("common:close")}
              title={active ? t("busy") : t("common:close")}
            >
              <X size={14} aria-hidden="true" />
            </button>
          </div>
        </div>

        {showProgress ? (
          <Progress
            phase={finished ? "done" : starting ? "downloading" : phase}
            message={status.data?.message ?? ""}
            output={status.data?.output ?? []}
            current={
              current
                ? current.kind === "commit"
                  ? shortSha(current.ref)
                  : (current.ref ?? "v" + current.version)
                : ""
            }
            onBack={() => {
              setFinished(false);
              setDismissedFailure(true);
            }}
          />
        ) : (
          <>
            <section className="update-notes" aria-label={t("notes")}>
              {selected?.kind === "commit" && commit ? (
                <>
                  <div className="update-notes-title">
                    <GitCommitHorizontal size={16} aria-hidden="true" />
                    <strong>
                      {gitMode ? t("latestCommit") : t("devBuild")}{" "}
                      {shortSha(commit.sha)}
                    </strong>
                    {!gitMode && (
                      <span className="tag beta">{t("unreleased")}</span>
                    )}
                    {commit.url && (
                      <a
                        href={commit.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        aria-label={t("viewCommit")}
                      >
                        <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    )}
                  </div>
                  {gitMode && (
                    <p className="muted">
                      {t("gitHint", { branch: commit.branch })}
                    </p>
                  )}
                  <p className="muted">
                    {commit.base
                      ? t("ahead", {
                          base: commit.base,
                          count: commit.ahead_by,
                        })
                      : t("recent", { count: commit.commits.length })}
                    {commit.ahead_by > commit.commits.length &&
                      t("showingRecent", { count: commit.commits.length })}
                  </p>
                  <ul className="commit-list">
                    {commit.commits.map((row) => (
                      <li key={row.sha}>
                        <code>{shortSha(row.sha)}</code>
                        <span>{row.message}</span>
                        <small className="muted">{row.author}</small>
                      </li>
                    ))}
                  </ul>
                </>
              ) : release ? (
                <>
                  <div className="update-notes-title">
                    <Tag size={16} aria-hidden="true" />
                    <strong>{release.name}</strong>
                    {release.prerelease && (
                      <span className="tag beta">{t("beta")}</span>
                    )}
                    {release.url && (
                      <a
                        href={release.url}
                        target="_blank"
                        rel="noreferrer noopener"
                        aria-label={t("viewRelease")}
                      >
                        <ExternalLink size={13} aria-hidden="true" />
                      </a>
                    )}
                  </div>
                  {release.body.trim() ? (
                    <Markdown text={release.body} />
                  ) : (
                    <p className="muted">{t("noNotes")}</p>
                  )}
                </>
              ) : (
                  gitMode
                    ? preview.isPending
                    : releases.isPending || (showCommit && preview.isPending)
                ) ? (
                <LoadingBar label={t("checkingGithub")} />
              ) : releases.isError ? (
                <p role="alert">{releases.error.message}</p>
              ) : (
                <p className="muted">
                  {showBeta ? t("noReleases") : t("noStable")}
                </p>
              )}
            </section>

            <section className="update-picker" aria-label={t("picker")}>
              {!gitMode && (
                <div className="update-filters">
                  <label>
                    <input
                      type="checkbox"
                      checked={showBeta}
                      onChange={(event) => {
                        setShowBeta(event.target.checked);
                        setPage(1);
                      }}
                    />
                    {t("showBeta")}
                  </label>
                  <label>
                    <input
                      type="checkbox"
                      checked={showCommit}
                      onChange={(event) => {
                        setShowCommit(event.target.checked);
                        setPage(1);
                        if (event.target.checked)
                          setSelected({ kind: "commit" });
                      }}
                    />
                    {t("showCommit")}
                  </label>
                </div>
              )}
              <div
                className="update-versions"
                role="listbox"
                aria-label={t("versions")}
              >
                {includeCommit && page === 1 && (
                  <>
                    {preview.isPending && (
                      <LoadingBar label={t("loadingCommit")} />
                    )}
                    {preview.isError && (
                      <p role="alert">{preview.error.message}</p>
                    )}
                    {preview.isSuccess && !commit && (
                      <p className="muted">{t("commitIncluded")}</p>
                    )}
                    {commit && (
                      <button
                        type="button"
                        role="option"
                        aria-selected={selected?.kind === "commit"}
                        className={
                          "update-version" +
                          (selected?.kind === "commit" ? " selected" : "")
                        }
                        onClick={() => setSelected({ kind: "commit" })}
                      >
                        <GitCommitHorizontal size={15} aria-hidden="true" />
                        <span>
                          <strong>
                            {gitMode ? t("latestCommit") : t("devBuild")}{" "}
                            {shortSha(commit.sha)}
                          </strong>
                          <small>{commit.message}</small>
                        </span>
                        {!gitMode && (
                          <span className="tag beta">{t("unreleased")}</span>
                        )}
                        <small className="muted">
                          {formatDate(commit.date)}
                        </small>
                      </button>
                    )}
                  </>
                )}
                {items.map((row, index) => {
                  const isCurrent = current
                    ? isCurrentRelease(row, current)
                    : false;
                  const chosen =
                    selected?.kind === "release" && selected.tag === row.tag;
                  return (
                    <button
                      type="button"
                      role="option"
                      aria-selected={chosen}
                      key={row.tag}
                      className={"update-version" + (chosen ? " selected" : "")}
                      style={{ animationDelay: index * 30 + "ms" }}
                      onClick={() =>
                        setSelected({ kind: "release", tag: row.tag })
                      }
                    >
                      <Tag size={15} aria-hidden="true" />
                      <span>
                        <strong>{row.tag}</strong>
                        {row.name !== row.tag && <small>{row.name}</small>}
                      </span>
                      {isCurrent && (
                        <span className="tag current">{t("current")}</span>
                      )}
                      {!isCurrent &&
                        page === 1 &&
                        index === 0 &&
                        current &&
                        isNewer(row, current) && (
                          <span className="tag new">{t("new")}</span>
                        )}
                      {row.prerelease && (
                        <span className="tag beta">{t("beta")}</span>
                      )}
                      <small className="muted">
                        {formatDate(row.published_at)}
                      </small>
                    </button>
                  );
                })}
              </div>
              {!gitMode && (releases.data?.total ?? 0) > PAGE_SIZE && (
                <Pagination
                  label={t("versionPages")}
                  page={page}
                  total={releases.data?.total ?? 0}
                  pageSize={PAGE_SIZE}
                  loading={releases.isFetching}
                  onChange={setPage}
                />
              )}
              <div className="update-apply">
                {startError && <p role="alert">{startError}</p>}
                {applyBlocked && !startError && (
                  <small className="muted">{applyBlocked}</small>
                )}
                {confirming && target ? (
                  <div className="update-confirm">
                    {inspecting ? (
                      <LoadingBar label={t("comparing")} />
                    ) : inspectError ? (
                      <>
                        <p role="alert">{inspectError}</p>
                        <span>{t("compareFailed")}</span>
                        <div className="actions">
                          <button
                            type="button"
                            className="primary"
                            onClick={() => void beginConfirm()}
                          >
                            {t("retryCompare")}
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirming(false)}
                          >
                            {t("common:cancel")}
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        {inspection && inspection.files.length > 0 ? (
                          <div className="update-local">
                            <strong>
                              {t("overwrites", {
                                count: inspection.files.length,
                              })}
                            </strong>
                            <ul className="update-local-files">
                              {inspection.files.map((file) => (
                                <li key={file.path}>
                                  <code>{file.path}</code>
                                  <span className="tag">
                                    {file.kind === "modified"
                                      ? t("modified")
                                      : t("deleted")}
                                  </span>
                                </li>
                              ))}
                            </ul>
                            {inspection.patch ? (
                              <small className="muted">
                                {t("patchSaved")}
                                <code>{inspection.patch}</code>
                              </small>
                            ) : (
                              <small className="muted">
                                {t("patchFailed")}
                              </small>
                            )}
                          </div>
                        ) : (
                          <span>
                            <Trans
                              t={t}
                              i18nKey={
                                gitMode ? "applyFastForward" : "applyDownload"
                              }
                              values={{ target: target.label }}
                              components={{ code: <code /> }}
                            />
                          </span>
                        )}
                        <div className="actions">
                          <button
                            type="button"
                            className="primary"
                            onClick={() => void apply()}
                            disabled={starting}
                          >
                            {inspection && inspection.files.length > 0
                              ? t("updateAnyway")
                              : gitMode
                                ? t("confirmUpdate")
                                : t("confirmSwitch")}
                          </button>
                          <button
                            type="button"
                            onClick={() => setConfirming(false)}
                            disabled={starting}
                          >
                            {t("common:cancel")}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ) : (
                  <button
                    type="button"
                    className="primary"
                    disabled={
                      !target || selectedIsCurrent || Boolean(applyBlocked)
                    }
                    onClick={() => void beginConfirm()}
                  >
                    {selectedIsCurrent
                      ? t("alreadyCurrent")
                      : target
                        ? t(gitMode ? "updateTo" : "switchTo", {
                            target: target.label,
                          })
                        : t("choose")}
                  </button>
                )}
              </div>
            </section>
          </>
        )}
      </div>
    </div>
  );
}

function Progress({
  phase,
  message,
  output,
  current,
  onBack,
}: {
  phase: UpdatePhase | "done";
  message: string;
  output: string[];
  current: string;
  onBack: () => void;
}) {
  const { t } = useTranslation("update");
  const log = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (log.current) log.current.scrollTop = log.current.scrollHeight;
  }, [output.length]);
  const index = STEPS.findIndex((step) => step === phase);
  const done = phase === "done";
  const failed = phase === "failed";
  return (
    <section className="update-progress" aria-live="polite">
      {done ? (
        <div className="update-result ok">
          <CircleCheck size={36} aria-hidden="true" />
          <strong>{t("done")}</strong>
          <span className="muted">{t("currentIs", { current })}</span>
        </div>
      ) : failed ? (
        <div className="update-result failed">
          <CircleX size={36} aria-hidden="true" />
          <strong>{t("failed")}</strong>
          <span role="alert">{message}</span>
          <span className="muted">{t("failedHint")}</span>
        </div>
      ) : (
        <ol className="update-steps">
          {STEPS.map((step, i) => (
            <li
              key={step}
              className={i < index ? "done" : i === index ? "active" : ""}
            >
              <span className="update-step-dot" aria-hidden="true" />
              {t(`steps.${step}`)}
            </li>
          ))}
        </ol>
      )}
      {!done && !failed && <LoadingBar label={message || t("updating")} />}
      {output.length > 0 && !done && (
        <pre ref={log} className="update-log">
          {output.join("\n")}
        </pre>
      )}
      {(done || failed) && (
        <div className="actions">
          <button type="button" onClick={onBack}>
            {t("back")}
          </button>
        </div>
      )}
    </section>
  );
}
