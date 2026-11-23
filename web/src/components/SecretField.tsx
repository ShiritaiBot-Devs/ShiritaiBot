import { useState } from "react";
import { Eye, EyeOff } from "lucide-react";
import { isSecretRef } from "../lib/utils";
import i18n from "../i18n";

export function SecretField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [visible, setVisible] = useState(false);
  const saved = isSecretRef(value);
  return (
    <div className="secret-field">
      <small className="field-help">{i18n.t("secret.help")}</small>
      <div className="inline">
        <input
          aria-label={i18n.t("secret.label")}
          type={visible ? "text" : "password"}
          autoComplete="new-password"
          value={saved ? "" : value}
          placeholder={saved ? i18n.t("secret.saved") : i18n.t("secret.empty")}
          onChange={(event) => onChange(event.target.value)}
        />
        <button
          type="button"
          aria-label={visible ? i18n.t("secret.hide") : i18n.t("secret.show")}
          onClick={() => setVisible((current) => !current)}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
        {value && (
          <button type="button" onClick={() => onChange("")}>
            {i18n.t("secret.clear")}
          </button>
        )}
      </div>
    </div>
  );
}
