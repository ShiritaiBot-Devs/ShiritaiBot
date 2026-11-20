import type {
  PermissionPreset,
  RiskPolicy,
  ToolInfo,
  ToolPermission,
  ToolPolicy,
  ToolRisk,
} from "../api/types";

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
    label: "安全",
    description: "只读查询、搜索与状态查看，无副作用",
  },
  normal: {
    label: "一般",
    description: "局部写入且可恢复，如记记忆、建待办、写知识库",
  },
  sensitive: {
    label: "敏感",
    description: "对外发送消息/文件、安装技能或外部 MCP 默认工具",
  },
  dangerous: {
    label: "危险",
    description: "高危或不可逆操作，如 Shell、子代理、删除数据或踢人",
  },
};

export const PERMISSIONS: Record<ToolPermission, string> = {
  deny: "禁止使用",
  manual: "需要人工审批",
  auto: "模型自动审批",
  allow: "无需审批",
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
    label: "保守：仅安全免审，其余人工审批",
    shortLabel: "保守",
    hint: "安全免审，一般/敏感/危险均需人工审批",
    risks: {
      safe: "allow",
      normal: "manual",
      sensitive: "manual",
      dangerous: "manual",
    },
  },
  {
    value: "balanced",
    label: "平衡：安全与一般免审，敏感自动，危险人工",
    shortLabel: "平衡",
    hint: "安全与一般免审，敏感模型自动审批，危险人工审批",
    risks: {
      safe: "allow",
      normal: "allow",
      sensitive: "auto",
      dangerous: "manual",
    },
  },
  {
    value: "autonomous",
    label: "高自治：安全与一般免审，敏感与危险自动审批",
    shortLabel: "高自治",
    hint: "安全与一般免审，敏感与危险由模型自动审批",
    risks: {
      safe: "allow",
      normal: "allow",
      sensitive: "auto",
      dangerous: "auto",
    },
  },
  {
    value: "full_access",
    label: "完全放行：所有等级无需审批",
    shortLabel: "完全放行",
    hint: "所有风险等级工具无需审批",
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
