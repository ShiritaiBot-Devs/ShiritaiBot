import "i18next";
import type { resources } from "./index";

// Typed keys: `t("settings:language.typo")` fails `tsc -b`.
declare module "i18next" {
  interface CustomTypeOptions {
    defaultNS: "common";
    resources: (typeof resources)["zh-CN"];
    interpolationPrefix: "{";
    interpolationSuffix: "}";
  }
}
