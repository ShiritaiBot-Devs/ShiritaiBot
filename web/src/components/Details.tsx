import { ChevronRight } from "lucide-react";
import i18n from "../i18n";

export function Details({
  value,
  summary = i18n.t("showDetails"),
}: {
  value: unknown;
  summary?: string;
}) {
  return (
    <details className="details">
      <summary>
        {summary}
        <ChevronRight size={12} aria-hidden="true" />
      </summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
