import { Circle } from "lucide-react";
import i18n from "../i18n";

export function EmptyState({ text = i18n.t("empty") }: { text?: string }) {
  return (
    <div className="empty">
      <Circle size={26} aria-hidden="true" />
      <p>{text}</p>
    </div>
  );
}
