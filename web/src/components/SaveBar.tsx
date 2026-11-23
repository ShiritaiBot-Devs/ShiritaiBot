import { Save } from "lucide-react";
import i18n from "../i18n";

export function SaveBar({
  busy,
  onSave,
  onDiscard,
}: {
  busy: boolean;
  onSave: () => void;
  onDiscard: () => void;
}) {
  return (
    <div
      className="save-bar"
      role="region"
      aria-label={i18n.t("saveBar.region")}
    >
      <span>{i18n.t("saveBar.message")}</span>
      <button type="button" onClick={onDiscard}>
        {i18n.t("saveBar.discard")}
      </button>
      <button
        type="button"
        className="primary"
        disabled={busy}
        onClick={onSave}
      >
        <Save size={15} />
        {i18n.t("saveBar.save")}
      </button>
    </div>
  );
}
