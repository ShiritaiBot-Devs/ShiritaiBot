import { LoadingBar } from "./LoadingBar";
import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { createClient } from "../api/client";
import { UiLanguageSelect } from "./LanguageSettings";
import { DesktopSidebarDrag } from "./DesktopTitleBar";

export function LoginView({
  authenticating = false,
  onAuthenticated,
  onError,
}: {
  authenticating?: boolean;
  onAuthenticated: (token: string) => void;
  onError: (message: string) => void;
}) {
  const { t } = useTranslation("shell");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      // Validate before persisting the token so a typo never sticks around.
      await createClient(() => token).health();
      onAuthenticated(token);
    } catch (error) {
      onError(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <DesktopSidebarDrag />
      <div className="brand-mark">
        <img src="/icon.png" alt="" />
      </div>
      <h1>ShiritaiBot</h1>
      <p className="muted">{t("login.hint")}</p>
      <form onSubmit={submit}>
        <label>
          {t("login.token")}
          <input
            autoFocus
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder={t("login.placeholder")}
            disabled={authenticating || busy}
            required
          />
        </label>
        <button
          type="submit"
          className="primary"
          disabled={authenticating || busy}
        >
          {authenticating || busy ? (
            <LoadingBar compact label={t("login.checking")} />
          ) : (
            t("login.submit")
          )}
          <ArrowUpRight size={16} />
        </button>
      </form>
      <div className="login-language">
        <UiLanguageSelect compact />
      </div>
    </div>
  );
}
