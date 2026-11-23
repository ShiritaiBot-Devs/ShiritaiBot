import type {
  PermissionPreset,
  RiskPolicy,
  ToolInfo,
  ToolPermission,
  ToolPolicy,
  ToolRisk,
} from "../api/types";
import i18n from "../i18n";

export const RISK_ORDER: ToolRisk[] = [
  "safe",
  "normal",
  "sensitive",
  "dangerous",
];

export const RISK_META: Record<
  ToolRisk,
  { label: string; description: string }
> = {
  safe: {
    get label() {
      return i18n.t("permissions:risk.safe.label");
    },
    get description() {
      return i18n.t("permissions:risk.safe.description");
    },
  },
  normal: {
    get label() {
      return i18n.t("permissions:risk.normal.label");
    },
    get description() {
      return i18n.t("permissions:risk.normal.description");
    },
  },
  sensitive: {
    get label() {
      return i18n.t("permissions:risk.sensitive.label");
    },
    get description() {
      return i18n.t("permissions:risk.sensitive.description");
    },
  },
  dangerous: {
    get label() {
      return i18n.t("permissions:risk.dangerous.label");
    },
    get description() {
      return i18n.t("permissions:risk.dangerous.description");
    },
  },
};

export const PERMISSIONS: Record<ToolPermission, string> = {
  get deny() {
    return i18n.t("permissions:permission.deny");
  },
  get manual() {
    return i18n.t("permissions:permission.manual");
  },
  get auto() {
    return i18n.t("permissions:permission.auto");
  },
  get allow() {
    return i18n.t("permissions:permission.allow");
  },
};

export interface PermissionPresetSpec {
  value: PermissionPreset;
  label: string;
  shortLabel: string;
  hint: string;
  risks: RiskPolicy;
}

export const PERMISSION_PRESETS: PermissionPresetSpec[] = [
  {
    value: "strict",
    get label() {
      return i18n.t("permissions:preset.strict.label");
    },
    get shortLabel() {
      return i18n.t("permissions:preset.strict.shortLabel");
    },
    get hint() {
      return i18n.t("permissions:preset.strict.hint");
    },
    risks: {
      safe: "allow",
      normal: "manual",
      sensitive: "manual",
      dangerous: "manual",
    },
  },
  {
    value: "balanced",
    get label() {
      return i18n.t("permissions:preset.balanced.label");
    },
    get shortLabel() {
      return i18n.t("permissions:preset.balanced.shortLabel");
    },
    get hint() {
      return i18n.t("permissions:preset.balanced.hint");
    },
    risks: {
      safe: "allow",
      normal: "allow",
      sensitive: "auto",
      dangerous: "manual",
    },
  },
  {
    value: "autonomous",
    get label() {
      return i18n.t("permissions:preset.autonomous.label");
    },
    get shortLabel() {
      return i18n.t("permissions:preset.autonomous.shortLabel");
    },
    get hint() {
      return i18n.t("permissions:preset.autonomous.hint");
    },
    risks: {
      safe: "allow",
      normal: "allow",
      sensitive: "auto",
      dangerous: "auto",
    },
  },
  {
    value: "full_access",
    get label() {
      return i18n.t("permissions:preset.full_access.label");
    },
    get shortLabel() {
      return i18n.t("permissions:preset.full_access.shortLabel");
    },
    get hint() {
      return i18n.t("permissions:preset.full_access.hint");
    },
    risks: {
      safe: "allow",
      normal: "allow",
      sensitive: "allow",
      dangerous: "allow",
    },
  },
];

export const DEFAULT_RISKS: RiskPolicy = {
  ...PERMISSION_PRESETS.find((item) => item.value === "balanced")!.risks,
};

export function normalizeRisks(risks?: Partial<RiskPolicy>): RiskPolicy {
  return {
    safe: risks?.safe ?? DEFAULT_RISKS.safe,
    normal: risks?.normal ?? DEFAULT_RISKS.normal,
    sensitive: risks?.sensitive ?? DEFAULT_RISKS.sensitive,
    dangerous: risks?.dangerous ?? DEFAULT_RISKS.dangerous,
  };
}

export function presetRisks(preset: PermissionPreset): RiskPolicy {
  const found = PERMISSION_PRESETS.find((item) => item.value === preset);
  return { ...(found?.risks ?? DEFAULT_RISKS) };
}

export function matchPreset(
  risks?: Partial<RiskPolicy>,
): PermissionPreset | "custom" {
  const normalized = normalizeRisks(risks);
  const found = PERMISSION_PRESETS.find((item) =>
    RISK_ORDER.every((level) => item.risks[level] === normalized[level]),
  );
  return found ? found.value : "custom";
}

export function toolRisk(tool?: ToolInfo): ToolRisk {
  const risk = tool?.risk;
  if (
    risk === "safe" ||
    risk === "normal" ||
    risk === "sensitive" ||
    risk === "dangerous"
  ) {
    return risk;
  }
  return "dangerous";
}

export function inheritedPermission(
  policy: ToolPolicy,
  tool?: ToolInfo,
): ToolPermission {
  return normalizeRisks(policy.risks)[toolRisk(tool)];
}
