/**
 * WebUI translations (docs/I18N.md).
 *
 * The interface language is per browser: a saved choice wins, then the instance's bot
 * language. Until that is known (before sign-in) it is `zh-CN`; the browser's own language
 * is never guessed. Catalogs are bundled, so switching is synchronous. `zh-CN` is also
 * the reference catalog and the fallback for missing keys.
 */
/// <reference types="vite/client" />
import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import { reference } from "../locales/reference";

export const LOCALES = ["zh-CN", "en"] as const;
export type Locale = (typeof LOCALES)[number];
/** Each language is named in itself so people can find their own. */
export const LOCALE_NAMES: Record<Locale, string> = {
  "zh-CN": "简体中文", // i18n: ok
  en: "English",
};
export const DEFAULT_LOCALE: Locale = "zh-CN";
const STORAGE_KEY = "shiritaibot.ui-language";

/** Every bundled catalog, as `resources[locale][namespace]`. */
export const resources: Record<string, Record<string, object>> = {};
const catalogs = import.meta.glob<object>("../locales/*/*.json", {
  eager: true,
  import: "default",
});
for (const [path, catalog] of Object.entries(catalogs)) {
  const [, locale, namespace] = /\/([^/]+)\/([^/]+)\.json$/.exec(path)!;
  (resources[locale] ??= {})[namespace] = catalog;
}

export function normalizeLocale(
  value: string | null | undefined,
): Locale | null {
  if (!value) return null;
  const folded = value.trim().toLowerCase().replace("_", "-");
  if (folded === "zh" || folded.startsWith("zh-")) return "zh-CN";
  if (folded === "en" || folded.startsWith("en-")) return "en";
  return null;
}

function savedLocale(): Locale | null {
  try {
    return normalizeLocale(localStorage.getItem(STORAGE_KEY));
  } catch {
    return null;
  }
}

function syncDocument(locale: string) {
  document.documentElement.lang = locale;
  document.title = i18n.t("shell:documentTitle");
}

void i18n.use(initReactI18next).init({
  resources,
  lng: savedLocale() ?? DEFAULT_LOCALE,
  fallbackLng: DEFAULT_LOCALE,
  supportedLngs: LOCALES,
  defaultNS: "common",
  ns: Object.keys(reference),
  // Catalog text uses {name} placeholders, the same syntax as the Python backend.
  interpolation: { escapeValue: false, prefix: "{", suffix: "}" },
});
syncDocument(i18n.language);
i18n.on("languageChanged", syncDocument);

export function currentLocale(): Locale {
  return normalizeLocale(i18n.language) ?? DEFAULT_LOCALE;
}

/** Applies the instance's bot language unless this browser has its own choice. */
export function applyInstanceLocale(value: string | null | undefined) {
  const locale = normalizeLocale(value);
  if (!savedLocale() && locale && locale !== currentLocale()) {
    void i18n.changeLanguage(locale);
  }
}

/** Switches and remembers the interface language for this browser. */
export function setUiLocale(locale: Locale) {
  try {
    localStorage.setItem(STORAGE_KEY, locale);
  } catch {
    // Private windows can refuse storage; the switch still applies to this tab.
  }
  void i18n.changeLanguage(locale);
}

/**
 * Translates `text` when it is a catalog key ("ns:key"), else returns it unchanged.
 * Lets components accept keys while untranslated Chinese literals still render as-is,
 * so pages can be migrated one at a time.
 */
export function tx(text: string): string;
export function tx(text: string | undefined): string | undefined;
export function tx(text: string | undefined): string | undefined {
  if (!text || !/^[a-z]+:[A-Za-z0-9_.]+$/.test(text) || !i18n.exists(text)) {
    return text;
  }
  return i18n.t(text as never) as string;
}

export default i18n;
