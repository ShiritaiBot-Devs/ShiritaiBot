import { LoadingBar } from "../components/LoadingBar";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Search, Trash2, Upload } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { KnowledgeDocument, KnowledgeHit } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { FieldForm } from "../components/FieldForm";
import { KnowledgeLibraries } from "../components/KnowledgeLibraries";
import { Pagination } from "../components/Pagination";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { KNOWLEDGE_FIELDS } from "../config/schema";
import type { Action, SessionConfigPageProps } from "./common";
import i18n, { currentLocale, tx } from "../i18n";

const PAGE_SIZE = 20;
const CHUNK_PAGE = 20;
const ACCEPT =
  ".txt,.md,.markdown,.rst,.log,.json,.yaml,.yml,.xml,.csv,.tsv,.pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";
const SOURCES: Record<string, string> = {
  web: "knowledge:sources.web",
  qq: "knowledge:sources.qq",
  agent: "knowledge:sources.agent",
};
const MATCHES: Record<KnowledgeHit["match"], string> = {
  hybrid: "knowledge:matches.hybrid",
  keyword: "knowledge:matches.keyword",
  semantic: "knowledge:matches.semantic",
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function KnowledgePage(props: SessionConfigPageProps) {
  const { client, act, config, setConfig } = props;
  const [selected, setSelected] = useState("default");
  const profiles = useQuery({
    queryKey: ["knowledge-libraries"],
    queryFn: () => client.knowledgeLibraries(),
  });
  const libraries = profiles.data ?? [
    { id: "default", name: i18n.t("knowledge:defaultLibrary") },
  ];
  const library = libraries.some((entry) => entry.id === selected)
    ? selected
    : "default";

  return (
    <>
      <KnowledgeLibraries {...props} />
      <section className="panel">
        <h2>{i18n.t("knowledge:settings.title")}</h2>
        <FieldForm
          value={config.knowledge}
          fields={KNOWLEDGE_FIELDS}
          onChange={(next) => setConfig({ ...config, knowledge: next })}
        />
      </section>
      <section className="panel">
        <label className="field">
          <span className="field-name">
            {i18n.t("knowledge:libraries.title")}
          </span>
          <select
            value={library}
            onChange={(event) => setSelected(event.target.value)}
          >
            {libraries.map((entry) => (
              <option key={entry.id} value={entry.id}>
                {entry.name}
              </option>
            ))}
          </select>
        </label>
        {profiles.error && (
          <QueryErrorNotice
            message={i18n.t("knowledge:libraries.loadFailed", {
              error: profiles.error.message,
            })}
          />
        )}
      </section>
      <KnowledgeContent
        key={library}
        client={client}
        act={act}
        library={library}
        limit={config.knowledge.max_file_mb}
      />
    </>
  );
}

function KnowledgeContent({
  client,
  act,
  library,
  limit,
}: {
  client: ApiClient;
  act: Action;
  library: string;
  limit: number;
}) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const listing = useQuery({
    queryKey: ["knowledge", library, query, page],
    queryFn: () => client.knowledge(query, page, PAGE_SIZE, library),
    refetchInterval: 15000,
  });
  const data = listing.data;

  return (
    <>
      <SearchPanel client={client} library={library} />
      <section className="panel">
        <h2>{i18n.t("knowledge:documents.title")}</h2>
        {data && (
          <p className="muted">
            {i18n.t("knowledge:documents.summary", {
              documents: i18n.t("knowledge:documents.count", {
                count: data.documents,
              }),
              chunks: i18n.t("knowledge:chunks", { count: data.chunks }),
              size: formatBytes(data.bytes),
            })}{" "}
            {data.index.model
              ? i18n.t("knowledge:documents.indexed", {
                  indexed: data.index.indexed,
                  pending: data.index.pending,
                })
              : i18n.t("knowledge:documents.noEmbedding")}
          </p>
        )}
        <UploadForm client={client} act={act} limit={limit} library={library} />
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
          }}
        >
          <input
            aria-label={i18n.t("knowledge:filter.label")}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={i18n.t("knowledge:filter.placeholder")}
          />
          <button type="submit">
            <Search size={16} />
            {i18n.t("knowledge:filter.submit")}
          </button>
        </form>
        {listing.error && (
          <QueryErrorNotice
            message={i18n.t("knowledge:documents.loadFailed", {
              error: listing.error.message,
            })}
          />
        )}
        {data && !data.items.length ? (
          <EmptyState
            text={
              query
                ? i18n.t("knowledge:documents.noMatch")
                : i18n.t("knowledge:documents.empty")
            }
          />
        ) : (
          <div className="kb-documents">
            {data?.items.map((row) => (
              <DocumentRow
                key={row.id}
                client={client}
                act={act}
                row={row}
                library={library}
              />
            ))}
          </div>
        )}
        {data && (
          <Pagination
            label={i18n.t("knowledge:documents.pages")}
            page={data.page}
            total={data.total}
            pageSize={PAGE_SIZE}
            onChange={setPage}
            loading={listing.isFetching}
          />
        )}
      </section>
    </>
  );
}

function UploadForm({
  client,
  act,
  limit,
  library,
}: {
  client: ApiClient;
  act: Action;
  limit: number;
  library: string;
}) {
  const [files, setFiles] = useState<File[]>([]);
  const [reset, setReset] = useState(0);
  return (
    <form
      className="kb-upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (!files.length) return;
        void act(async () => {
          // One at a time so that a bad file names itself and the rest stay queued.
          for (const [index, file] of files.entries()) {
            try {
              await client.uploadKnowledge(file, library);
            } catch (error) {
              setFiles(files.slice(index));
              throw new Error(
                i18n.t("knowledge:upload.failed", {
                  file: file.name,
                  error:
                    error instanceof Error
                      ? error.message
                      : i18n.t("knowledge:upload.failedShort"),
                }),
                { cause: error },
              );
            }
          }
          setFiles([]);
          setReset((value) => value + 1);
        }, i18n.t("knowledge:upload.done"));
      }}
    >
      <label className="field">
        <span className="field-name">
          {i18n.t("knowledge:upload.label", { limit })}
        </span>
        <input
          key={reset}
          aria-label={i18n.t("knowledge:upload.files")}
          type="file"
          multiple
          accept={ACCEPT}
          onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
        />
        <small className="muted">{i18n.t("knowledge:upload.formats")}</small>
      </label>
      <button type="submit" className="primary" disabled={!files.length}>
        <Upload size={16} />
        {files.length > 1
          ? i18n.t("knowledge:upload.submitMany", { count: files.length })
          : i18n.t("knowledge:upload.submit")}
      </button>
    </form>
  );
}

function SearchPanel({
  client,
  library,
}: {
  client: ApiClient;
  library: string;
}) {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const hits = useQuery({
    queryKey: ["knowledge-search", library, query],
    queryFn: () => client.knowledgeSearch(query, library),
    enabled: Boolean(query),
  });
  return (
    <section className="panel">
      <h2>{i18n.t("knowledge:search.title")}</h2>
      <p className="muted">{i18n.t("knowledge:search.hint")}</p>
      <form
        className="inline"
        onSubmit={(event) => {
          event.preventDefault();
          setQuery(draft.trim());
        }}
      >
        <input
          aria-label={i18n.t("knowledge:search.label")}
          value={draft}
          maxLength={500}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={i18n.t("knowledge:search.placeholder")}
        />
        <button type="submit" disabled={!draft.trim()}>
          <Search size={16} />
          {i18n.t("knowledge:search.submit")}
        </button>
      </form>
      {hits.isFetching && (
        <LoadingBar label={i18n.t("knowledge:search.label")} />
      )}
      {hits.error && (
        <QueryErrorNotice
          message={i18n.t("knowledge:search.failed", {
            error: hits.error.message,
          })}
        />
      )}
      {hits.data && !hits.data.length && (
        <EmptyState text={i18n.t("knowledge:search.empty")} />
      )}
      {hits.data?.map((hit) => (
        <article className="kb-chunk" key={`${hit.document}#${hit.seq}`}>
          <div className="kb-chunk-head">
            <strong>{hit.title}</strong>
            {hit.label && <span className="muted">{hit.label}</span>}
            <code>
              {hit.document}#{hit.seq}
            </code>
            <span className="tag">{tx(MATCHES[hit.match])}</span>
          </div>
          <p>{hit.text}</p>
        </article>
      ))}
    </section>
  );
}

function DocumentRow({
  client,
  act,
  row,
  library,
}: {
  client: ApiClient;
  act: Action;
  row: KnowledgeDocument;
  library: string;
}) {
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  return (
    <article className="kb-document">
      <div className="kb-document-head">
        <div className="kb-document-title">
          <strong>{row.title}</strong>
          <span className="muted">
            <code>{row.id}</code> · {row.filename} · {formatBytes(row.bytes)} ·{" "}
            {i18n.t("knowledge:chunks", { count: row.chunks })} ·{" "}
            {tx(SOURCES[row.source] ?? row.source)} ·{" "}
            {new Date(row.created * 1000).toLocaleDateString(currentLocale())}
          </span>
        </div>
        <div className="actions">
          <button
            type="button"
            aria-expanded={open}
            onClick={() => setOpen((value) => !value)}
          >
            {open
              ? i18n.t("knowledge:collapse")
              : i18n.t("knowledge:showChunks")}
          </button>
          <button
            type="button"
            aria-label={i18n.t("knowledge:download", { title: row.title })}
            onClick={() =>
              void act(async () => {
                const url = URL.createObjectURL(
                  await client.knowledgeFile(row.id, library),
                );
                const link = document.createElement("a");
                link.href = url;
                link.download = row.filename;
                link.click();
                URL.revokeObjectURL(url);
              }, "")
            }
          >
            <Download size={16} />
          </button>
          {confirming ? (
            <>
              <button
                type="button"
                className="danger"
                onClick={() =>
                  void act(
                    () => client.deleteKnowledge(row.id, library),
                    i18n.t("knowledge:deleted"),
                  )
                }
              >
                {i18n.t("knowledge:confirmDelete")}
              </button>
              <button type="button" onClick={() => setConfirming(false)}>
                {i18n.t("knowledge:cancel")}
              </button>
            </>
          ) : (
            <button
              type="button"
              className="danger"
              aria-label={i18n.t("knowledge:delete", { title: row.title })}
              onClick={() => setConfirming(true)}
            >
              <Trash2 size={16} />
            </button>
          )}
        </div>
      </div>
      {open && (
        <ChunkList
          client={client}
          id={row.id}
          total={row.chunks}
          library={library}
        />
      )}
    </article>
  );
}

function ChunkList({
  client,
  id,
  total,
  library,
}: {
  client: ApiClient;
  id: string;
  total: number;
  library: string;
}) {
  const [page, setPage] = useState(1);
  const detail = useQuery({
    queryKey: ["knowledge-document", library, id, page],
    queryFn: () =>
      client.knowledgeDocument(
        id,
        (page - 1) * CHUNK_PAGE,
        CHUNK_PAGE,
        library,
      ),
  });
  return (
    <div className="kb-chunks">
      {detail.error && (
        <QueryErrorNotice
          message={i18n.t("knowledge:chunksFailed", {
            error: detail.error.message,
          })}
        />
      )}
      {detail.data?.items.map((chunk) => (
        <article className="kb-chunk" key={chunk.seq}>
          <div className="kb-chunk-head">
            <code>#{chunk.seq}</code>
            {chunk.label && <span className="muted">{chunk.label}</span>}
          </div>
          <p>{chunk.text}</p>
        </article>
      ))}
      <Pagination
        label={i18n.t("knowledge:chunkPages", { id })}
        page={page}
        total={total}
        pageSize={CHUNK_PAGE}
        onChange={setPage}
        loading={detail.isFetching}
      />
    </div>
  );
}
