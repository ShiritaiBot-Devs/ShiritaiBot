import { Check, X } from "lucide-react";
import type { ApprovalRecord } from "../api/types";
import { formatDateTime } from "../lib/utils";
import i18n from "../i18n";

export function ApprovalCard({
  approval,
  busy,
  onResolve,
}: {
  approval: ApprovalRecord;
  busy: boolean;
  onResolve: (allow: boolean) => void;
}) {
  return (
    <article className="approval">
      <div>
        <span className="eyebrow">
          {i18n.t("chat:approval.needsYou", { id: approval.id })}
        </span>
        <h3>{approval.tool}</h3>
        <pre>{JSON.stringify(approval.args, null, 2)}</pre>
        <p className="muted">
          {approval.scope} ·{" "}
          {i18n.t("chat:approval.validUntil", {
            time: formatDateTime(approval.expires),
          })}
        </p>
      </div>
      <div className="actions">
        <button
          type="button"
          className="primary"
          disabled={busy}
          onClick={() => onResolve(true)}
        >
          <Check size={14} />
          {i18n.t("chat:approval.allowOnce")}
        </button>
        <button type="button" disabled={busy} onClick={() => onResolve(false)}>
          <X size={14} />
          {i18n.t("chat:approval.deny")}
        </button>
      </div>
    </article>
  );
}
