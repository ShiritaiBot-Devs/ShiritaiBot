import { ChevronLeft, ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { LoadingBar } from "./LoadingBar";

export const PAGE_SIZE = 20;

export function Pagination({
  label,
  page,
  total,
  pageSize = PAGE_SIZE,
  onChange,
  loading = false,
}: {
  label: string;
  page: number;
  total: number;
  pageSize?: number;
  onChange: (page: number) => void;
  loading?: boolean;
}) {
  const { t } = useTranslation();
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pages);
  return (
    <nav className="pagination" aria-label={t("pagination.region", { label })}>
      {loading && <LoadingBar label={t("pagination.loading", { label })} />}
      <small className="muted" aria-live="polite">
        {t("pagination.summary", { count: total, current, pages })}
      </small>
      <div className="actions">
        <button
          type="button"
          disabled={loading || current <= 1}
          onClick={() => onChange(current - 1)}
          aria-label={t("pagination.previousLabel", { label })}
        >
          <ChevronLeft size={14} /> {t("pagination.previous")}
        </button>
        <button
          type="button"
          disabled={loading || current >= pages}
          onClick={() => onChange(current + 1)}
          aria-label={t("pagination.nextLabel", { label })}
        >
          {t("pagination.next")} <ChevronRight size={14} />
        </button>
      </div>
    </nav>
  );
}
