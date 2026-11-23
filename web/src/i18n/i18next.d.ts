import "i18next";
import type { reference } from "../locales/reference";

// Typed keys: `t("settings:language.typo")` fails `tsc -b`.
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: typeof reference;
    interpolationPrefix: "{";
    interpolationSuffix: "}";
  }
}
