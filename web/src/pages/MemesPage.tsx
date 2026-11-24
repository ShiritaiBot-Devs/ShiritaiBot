import { MemeImage } from "../components/MemeImage";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RefreshCw, Search, Trash2, Upload } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { MemeCategory, MemeRecord } from "../api/types";
import { EmptyState } from "../components/EmptyState";
import { FieldForm } from "../components/FieldForm";
import { Pagination } from "../components/Pagination";
import { QueryErrorNotice } from "../components/QueryErrorNotice";
import { MEME_FIELDS } from "../config/schema";
import type { Action, ConfigPageProps } from "./common";
import i18n, { currentLocale, tx } from "../i18n";

const PAGE_SIZE = 24;
const CATEGORY_NAME = /^[a-z][a-z0-9_]{0,31}$/;
const SOURCES: Record<string, string> = {
  group: "memes:sources.group",
  agent: "memes:sources.agent",
  manual: "memes:sources.manual",
};

export function MemesPage({ client, act, config, setConfig }: ConfigPageProps) {
  const meme = config.meme;
  const [category, setCategory] = useState("");
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const listing = useQuery({
    queryKey: ["memes", category, query, page],
    queryFn: () => client.memes(category, query, page, PAGE_SIZE),
    refetchInterval: 15000,
  });
  const counts = listing.data?.counts ?? {};

  function setCategories(categories: MemeCategory[]) {
    setConfig({ ...config, meme: { ...meme, categories } });
  }

  return (
    <>
      <section className="panel">
        <h2>{i18n.t("memes:title")}</h2>
        <p className="muted">{i18n.t("memes:intro")}</p>
        <FieldForm
          value={meme}
          fields={MEME_FIELDS}
          onChange={(next) => setConfig({ ...config, meme: next })}
        />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>{i18n.t("memes:categories.title")}</h2>
          <button
            type="button"
            disabled={meme.categories.length >= 30}
            onClick={() =>
              setCategories([
                ...meme.categories,
                { name: uniqueName(meme.categories), description: "" },
              ])
            }
          >
            <Plus size={16} />
            {i18n.t("memes:categories.add")}
          </button>
        </div>
        <p className="muted">{i18n.t("memes:categories.hint")}</p>
        <div className="meme-categories">
          {meme.categories.map((item, index) => {
            const invalid = !CATEGORY_NAME.test(item.name);
            const duplicate = meme.categories.some(
              (other, position) =>
                position !== index && other.name === item.name,
            );
            return (
              <div className="meme-category" key={index}>
                <input
                  aria-label={i18n.t("memes:categories.nameLabel", {
                    n: index + 1,
                  })}
                  aria-invalid={invalid || duplicate}
                  value={item.name}
                  onChange={(event) =>
                    setCategories(
                      meme.categories.map((row, position) =>
                        position === index
                          ? { ...row, name: event.target.value.trim() }
                          : row,
                      ),
                    )
                  }
                />
                <input
                  aria-label={i18n.t("memes:categories.descriptionLabel", {
                    n: index + 1,
                  })}
                  value={item.description}
                  maxLength={200}
                  placeholder={i18n.t(
                    "memes:categories.descriptionPlaceholder",
                  )}
                  onChange={(event) =>
                    setCategories(
                      meme.categories.map((row, position) =>
                        position === index
                          ? { ...row, description: event.target.value }
                          : row,
                      ),
                    )
                  }
                />
                <span className="tag">
                  {i18n.t("memes:count", { count: counts[item.name] ?? 0 })}
                </span>
                <button
                  type="button"
                  className="danger"
                  aria-label={i18n.t("memes:categories.delete", {
                    name: item.name,
                  })}
                  disabled={meme.categories.length <= 1}
                  onClick={() =>
                    setCategories(
                      meme.categories.filter(
                        (_, position) => position !== index,
                      ),
                    )
                  }
                >
                  <Trash2 size={16} />
                </button>
                {(invalid || duplicate) && (
                  <small role="alert">
                    {invalid
                      ? i18n.t("memes:categories.invalidName")
                      : i18n.t("memes:categories.duplicate")}
                  </small>
                )}
              </div>
            );
          })}
        </div>
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>{i18n.t("memes:library.title")}</h2>
          {listing.data && (
            <span className="tag">
              {i18n.t("memes:library.usage", {
                count: listing.data.count,
                capacity: listing.data.capacity,
              })}{" "}
              {listing.data.index.model
                ? i18n.t("memes:library.indexed", {
                    indexed: listing.data.index.indexed,
                    pending: listing.data.index.pending,
                  })
                : i18n.t("memes:library.noEmbedding")}
            </span>
          )}
        </div>
        <UploadForm client={client} act={act} categories={meme.categories} />
        <div
          className="range-tabs"
          role="group"
          aria-label={i18n.t("memes:library.category")}
        >
          {[{ name: "", description: "" }, ...meme.categories].map((item) => (
            <button
              type="button"
              key={item.name || "all"}
              className={category === item.name ? "selected" : ""}
              aria-pressed={category === item.name}
              onClick={() => {
                setCategory(item.name);
                setPage(1);
              }}
            >
              {item.name || i18n.t("memes:library.all")}
              {item.name && ` (${counts[item.name] ?? 0})`}
            </button>
          ))}
        </div>
        <form
          className="inline"
          onSubmit={(event) => {
            event.preventDefault();
            setQuery(draft.trim());
            setPage(1);
          }}
        >
          <input
            aria-label={i18n.t("memes:search.label")}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder={i18n.t("memes:search.placeholder")}
          />
          <button type="submit">
            <Search size={16} />
            {i18n.t("memes:search.submit")}
          </button>
        </form>
        {listing.error && (
          <QueryErrorNotice
            message={i18n.t("memes:loadFailed", {
              error: listing.error.message,
            })}
          />
        )}
        {listing.data && !listing.data.items.length ? (
          <EmptyState
            text={
              query || category
                ? i18n.t("memes:noMatch")
                : i18n.t("memes:empty")
            }
          />
        ) : (
          <div className="meme-grid">
            {listing.data?.items.map((row) => (
              <MemeCard
                key={row.id + row.category + row.description}
                client={client}
                act={act}
                row={row}
                categories={meme.categories}
              />
            ))}
          </div>
        )}
        {listing.data && (
          <Pagination
            label={i18n.t("memes:pages")}
            page={listing.data.page}
            total={listing.data.total}
            pageSize={PAGE_SIZE}
            onChange={setPage}
            loading={listing.isFetching}
          />
        )}
      </section>
    </>
  );
}

function uniqueName(rows: MemeCategory[]): string {
  let index = rows.length + 1;
  while (rows.some((row) => row.name === `category_${index}`)) index += 1;
  return `category_${index}`;
}

function UploadForm({
  client,
  act,
  categories,
}: {
  client: ApiClient;
  act: Action;
  categories: MemeCategory[];
}) {
  const [file, setFile] = useState<File | null>(null);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [reset, setReset] = useState(0);
  return (
    <form
      className="meme-upload"
      onSubmit={(event) => {
        event.preventDefault();
        if (!file) return;
        void act(async () => {
          await client.uploadMeme(file, category, description.trim());
          setFile(null);
          setDescription("");
          setReset((value) => value + 1);
        }, i18n.t("memes:uploaded"));
      }}
    >
      <label className="field">
        <span className="field-name">{i18n.t("memes:upload.title")}</span>
        <input
          key={reset}
          aria-label={i18n.t("memes:upload.image")}
          type="file"
          accept="image/png,image/jpeg,image/gif,image/webp"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <label className="field">
        <span className="field-name">{i18n.t("memes:upload.category")}</span>
        <select
          aria-label={i18n.t("memes:upload.categoryLabel")}
          value={category}
          onChange={(event) => setCategory(event.target.value)}
        >
          <option value="">{i18n.t("memes:upload.auto")}</option>
          {categories.map((item) => (
            <option key={item.name} value={item.name}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label className="field">
        <span className="field-name">{i18n.t("memes:upload.description")}</span>
        <input
          aria-label={i18n.t("memes:upload.descriptionLabel")}
          value={description}
          maxLength={300}
          placeholder={i18n.t("memes:upload.descriptionPlaceholder")}
          onChange={(event) => setDescription(event.target.value)}
        />
      </label>
      <button type="submit" className="primary" disabled={!file}>
        <Upload size={16} />
        {i18n.t("memes:upload.submit")}
      </button>
    </form>
  );
}

function MemeCard({
  client,
  act,
  row,
  categories,
}: {
  client: ApiClient;
  act: Action;
  row: MemeRecord;
  categories: MemeCategory[];
}) {
  const queryClient = useQueryClient();
  const [description, setDescription] = useState(row.description);
  const [confirming, setConfirming] = useState(false);
  const known = categories.some((item) => item.name === row.category);
  return (
    <article className="meme-card">
      <div className="meme-thumb">
        <MemeImage client={client} id={row.id} />
      </div>
      <div className="meme-meta">
        <code>{row.id}</code>
        <span className="muted">
          {tx(SOURCES[row.source] ?? row.source)} ·{" "}
          {new Date(row.created).toLocaleDateString(currentLocale())}
        </span>
      </div>
      <select
        aria-label={i18n.t("memes:item.categoryLabel", { id: row.id })}
        value={row.category}
        onChange={(event) =>
          void act(
            () => client.updateMeme(row.id, { category: event.target.value }),
            i18n.t("memes:item.categoryUpdated"),
          )
        }
      >
        {!known && (
          <option value={row.category}>
            {i18n.t("memes:item.unconfigured", { category: row.category })}
          </option>
        )}
        {categories.map((item) => (
          <option key={item.name} value={item.name}>
            {item.name}
          </option>
        ))}
      </select>
      <textarea
        aria-label={i18n.t("memes:item.descriptionLabel", { id: row.id })}
        value={description}
        maxLength={300}
        rows={3}
        onChange={(event) => setDescription(event.target.value)}
      />
      <div className="actions">
        <button
          type="button"
          disabled={
            !description.trim() || description.trim() === row.description
          }
          onClick={() =>
            void act(
              () => client.updateMeme(row.id, { description }),
              i18n.t("memes:item.descriptionSaved"),
            )
          }
        >
          {i18n.t("memes:item.saveDescription")}
        </button>
        <button
          type="button"
          title={i18n.t("memes:item.redescribeLabel")}
          onClick={() =>
            void act(
              () => client.describeMeme(row.id),
              i18n.t("memes:item.redescribed"),
            )
          }
        >
          <RefreshCw size={16} />
          {i18n.t("memes:item.redescribe")}
        </button>
        {confirming ? (
          <>
            <button
              type="button"
              className="danger"
              onClick={() =>
                void act(async () => {
                  await client.deleteMeme(row.id);
                  queryClient.removeQueries({
                    queryKey: ["meme-image", row.id],
                  });
                }, i18n.t("memes:item.deleted"))
              }
            >
              {i18n.t("memes:item.confirmDelete")}
            </button>
            <button type="button" onClick={() => setConfirming(false)}>
              {i18n.t("memes:cancel")}
            </button>
          </>
        ) : (
          <button
            type="button"
            className="danger"
            aria-label={i18n.t("memes:item.delete", { id: row.id })}
            onClick={() => setConfirming(true)}
          >
            <Trash2 size={16} />
          </button>
        )}
      </div>
    </article>
  );
}
