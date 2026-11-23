import { AlertCircle, CheckCircle2, TriangleAlert, X } from "lucide-react";
import { createPortal } from "react-dom";
import i18n from "../i18n";

export type NoticeTone = "success" | "error" | "warning";

export function Toast({
  message,
  tone = "success",
  onDismiss,
}: {
  message: string;
  tone?: NoticeTone;
  onDismiss: () => void;
}) {
  const Icon =
    tone === "error"
      ? AlertCircle
      : tone === "warning"
        ? TriangleAlert
        : CheckCircle2;
  return createPortal(
    <div
      role={tone === "success" ? "status" : "alert"}
      className={`toast toast--${tone}`}
      aria-atomic="true"
    >
      <Icon size={20} aria-hidden="true" className="toast-icon" />
      <span className="toast-message">{message}</span>
      <button
        type="button"
        aria-label={i18n.t("toast.dismiss")}
        onClick={onDismiss}
      >
        <X size={15} />
      </button>
    </div>,
    document.body,
  );
}
