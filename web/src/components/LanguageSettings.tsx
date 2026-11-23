import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { ApiClient } from "../api/client";
import { LOCALES, LOCALE_NAMES, currentLocale, setUiLocale } from "../i18n";
import type { Locale } from "../i18n";
import { sessionTitle } from "../lib/sessions";
import type { Action } from "../pages/common";
import { QueryErrorNotice } from "./QueryErrorNotice";

/** Per-browser interface language; separate from the bot language in config. */
export function UiLanguageSelect({ compact = false }: { compact?: boolean }) {
  const { t, i18n } = useTranslation(["common", "settings"]);
  const select = (
    <select
      aria-label={t("language.switch")}
      title={compact ? t("language.label") : undefined}
      className={compact ? "console-language" : undefined}
      value={currentLocale()}
      onChange={(event) => setUiLocale(event.target.value as Locale)}
      key={i18n.language}
    >
      {LOCALES.map((locale) => (
        <option key={locale} value={locale} lang={locale}>
          {LOCALE_NAMES[locale]}
        </option>
      ))}
    </select>
  );
  if (compact) return select;
  return (
    <div className="field">
      <span className="field-name">{t("language.label")}</span>
      <small className="field-help">{t("settings:language.uiHelp")}</small>
      {select}
    </div>
  );
}

/** Sessions that chose their own language with /lang, each clearable. */
export function SessionLocaleList({
  client,
  act,
}: {
  client: ApiClient;
  act: Action;
}) {
  const { t } = useTranslation("settings");
  const query = useQuery({
    queryKey: ["session-locales"],
    queryFn: () => client.sessionLocales(),
  });
  const rows = query.data ?? [];
  return (
    <div className="field full">
      <span className="field-name">{t("language.overridesTitle")}</span>
      <small className="field-help">{t("language.overridesHelp")}</small>
      {query.error ? (
        <>
          <QueryErrorNotice message={t("language.loadFailed")} />
          <p className="muted">{t("language.loadFailed")}</p>
        </>
      ) : rows.length === 0 ? (
        <p className="muted">{t("language.overridesEmpty")}</p>
      ) : (
        <div className="table-scroll">
          <table className="usage-table">
            <thead>
              <tr>
                <th scope="col">{t("language.session")}</th>
                <th scope="col">{t("language.locale")}</th>
                <th scope="col">
                  <span className="sr-only">{t("language.useDefault")}</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.session}>
                  <td title={row.session}>{sessionTitle(row.session)}</td>
                  <td lang={row.locale}>{LOCALE_NAMES[row.locale]}</td>
                  <td>
                    <button
                      type="button"
                      onClick={() =>
                        void act(
                          () => client.clearSessionLocale(row.session),
                          t("language.cleared", {
                            session: sessionTitle(row.session),
                          }),
                        )
                      }
                    >
                      {t("language.useDefault")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
