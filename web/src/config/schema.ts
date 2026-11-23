import type {
  AdapterConfig,
  McpServerConfig,
  PersonaConfig,
  ProviderConfig,
  SubagentConfig,
} from "../api/types";
import i18n, { LOCALE_NAMES } from "../i18n";
import { uniqueId } from "../lib/utils";
import { PROVIDER_PRESETS, type ProviderPreset } from "./provider-presets";

export type FieldKind =
  | "text"
  | "number"
  | "boolean"
  | "select"
  | "secret"
  | "write-only"
  | "list"
  | "time"
  | "json"
  | "multiselect"
  | "textarea"
  | "slider";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  help?: string;
  placeholder?: string;
  options?: string[];
  optionLabels?: Record<string, string>;
  min?: number;
  max?: number;
  step?: number;
  full?: boolean;
}

export const PROVIDER_FIELDS: FieldSpec[] = [
  {
    key: "id",
    label: "fields:provider.id.label",
    kind: "text",
    help: "fields:provider.id.help",
  },
  {
    key: "enabled",
    label: "fields:provider.enabled.label",
    kind: "boolean",
    help: "fields:provider.enabled.help",
  },
  {
    key: "protocol",
    label: "fields:provider.protocol.label",
    kind: "select",
    options: [
      "chat_completions",
      "responses",
      "anthropic",
      "gemini",
      "systemone",
    ],
    help: "fields:provider.protocol.help",
  },
  {
    key: "base_url",
    label: "API Base URL",
    kind: "text",
    placeholder: "https://api.openai.com/v1",
    help: "fields:provider.base_url.help",
  },
  { key: "api_key", label: "API Key", kind: "secret" },
  {
    key: "timeout",
    label: "fields:provider.timeout.label",
    kind: "number",
    min: 5,
    max: 600,
  },
  {
    key: "max_retries",
    label: "fields:provider.max_retries.label",
    kind: "number",
    min: 0,
    max: 10,
    help: "fields:provider.max_retries.help",
  },
  {
    key: "proxy",
    label: "fields:provider.proxy.label",
    kind: "text",
    placeholder: "http://127.0.0.1:7890",
    help: "fields:provider.proxy.help",
    full: true,
  },
];

const PRICE_FIELDS: FieldSpec[] = [
  {
    key: "currency",
    label: "fields:price.currency.label",
    kind: "select",
    options: ["USD", "CNY"],
    optionLabels: {
      USD: "fields:price.currency.options.USD",
      CNY: "fields:price.currency.options.CNY",
    },
    help: "fields:price.currency.help",
  },
  {
    key: "price_input",
    label: "fields:price.price_input.label",
    kind: "number",
    min: 0,
    max: 100000,
    step: 0.0001,
    help: "fields:price.price_input.help",
  },
  {
    key: "price_cached",
    label: "fields:price.price_cached.label",
    kind: "number",
    min: 0,
    max: 100000,
    step: 0.0001,
    help: "fields:price.price_cached.help",
  },
  {
    key: "price_output",
    label: "fields:price.price_output.label",
    kind: "number",
    min: 0,
    max: 100000,
    step: 0.0001,
  },
];

export const MODEL_FIELDS: FieldSpec[] = [
  { key: "id", label: "fields:model.id.label", kind: "text" },
  { key: "model", label: "fields:model.model.label", kind: "text" },
  {
    key: "max_output",
    label: "fields:model.max_output.label",
    kind: "number",
    min: 64,
    max: 128000,
    help: "fields:model.max_output.help",
  },
  {
    key: "enabled",
    label: "fields:model.enabled.label",
    kind: "boolean",
    help: "fields:model.enabled.help",
  },
  {
    key: "max_context",
    label: "fields:model.max_context.label",
    kind: "number",
    min: 0,
    max: 10000000,
    help: "fields:model.max_context.help",
  },
  {
    key: "modalities",
    label: "fields:model.modalities.label",
    kind: "multiselect",
    options: ["text", "image", "audio", "video"],
    optionLabels: {
      text: "fields:model.modalities.options.text",
      image: "fields:model.modalities.options.image",
      audio: "fields:model.modalities.options.audio",
      video: "fields:model.modalities.options.video",
    },
    help: "fields:model.modalities.help",
    full: true,
  },
  {
    key: "extra_body",
    label: "fields:model.extra_body.label",
    kind: "json",
    full: true,
    placeholder: '{"temperature": 0.7}',
    help: "fields:model.extra_body.help",
  },
  ...PRICE_FIELDS,
];

export const EMBEDDING_MODEL_FIELDS: FieldSpec[] = [
  { key: "id", label: "fields:embeddingModel.id.label", kind: "text" },
  {
    key: "model",
    label: "fields:embeddingModel.model.label",
    kind: "text",
    placeholder: "text-embedding-3-small",
  },
  {
    key: "enabled",
    label: "fields:embeddingModel.enabled.label",
    kind: "boolean",
    help: "fields:embeddingModel.enabled.help",
  },
  {
    key: "dims",
    label: "fields:embeddingModel.dims.label",
    kind: "number",
    min: 0,
    max: 16384,
    help: "fields:embeddingModel.dims.help",
  },
  {
    key: "extra_body",
    label: "fields:embeddingModel.extra_body.label",
    kind: "json",
    full: true,
    placeholder: '{"encoding_format": "float"}',
    help: "fields:embeddingModel.extra_body.help",
  },
  ...PRICE_FIELDS,
];

const ADAPTER_COMMON_FIELDS: FieldSpec[] = [
  { key: "id", label: "fields:adapterCommon.id.label", kind: "text" },
  {
    key: "type",
    label: "fields:adapterCommon.type.label",
    kind: "select",
    options: ["onebot11", "qq_official", "feishu"],
    optionLabels: {
      onebot11: "QQ · OneBot V11",
      qq_official: "fields:adapterCommon.type.options.qq_official",
      feishu: "fields:adapterCommon.type.options.feishu",
    },
  },
  {
    key: "enabled",
    label: "fields:adapterCommon.enabled.label",
    kind: "boolean",
    help: "fields:adapterCommon.enabled.help",
  },
];

const ONEBOT_FIELDS: FieldSpec[] = [
  {
    key: "url",
    label: "fields:onebot.url.label",
    kind: "text",
    placeholder: "ws://127.0.0.1:3001",
    help: "fields:onebot.url.help",
  },
  { key: "token", label: "fields:onebot.token.label", kind: "secret" },
  { key: "self_id", label: "fields:onebot.self_id.label", kind: "text" },
  {
    key: "owner_id",
    label: "fields:onebot.owner_id.label",
    kind: "text",
    help: "fields:onebot.owner_id.help",
  },
  {
    key: "groups",
    label: "fields:onebot.groups.label",
    kind: "list",
    help: "fields:onebot.groups.help",
  },
  {
    key: "friends",
    label: "fields:onebot.friends.label",
    kind: "list",
    help: "fields:onebot.friends.help",
  },
  {
    key: "ignore_users",
    label: "fields:onebot.ignore_users.label",
    kind: "list",
    help: "fields:onebot.ignore_users.help",
  },
  {
    key: "auto_start",
    label: "fields:onebot.auto_start.label",
    kind: "list",
    help: "fields:onebot.auto_start.help",
  },
  {
    key: "auto_start_after",
    label: "fields:onebot.auto_start_after.label",
    kind: "number",
    min: 5,
    max: 3600,
    help: "fields:onebot.auto_start_after.help",
  },
];

export function adapterFields(
  item: AdapterConfig,
  hasSavedSecret: boolean,
): FieldSpec[] {
  if (item.type === "qq_official") {
    return [
      ...ADAPTER_COMMON_FIELDS,
      {
        key: "app_id",
        label: "App ID",
        kind: "text",
        help: "fields:adapter.app_id.help",
      },
      {
        key: "app_secret",
        label: "App Secret",
        kind: "write-only",
        placeholder: hasSavedSecret
          ? "fields:adapter.app_secret.placeholder"
          : "fields:adapter.app_secret.placeholder2",
      },
      {
        key: "qq_sandbox",
        label: "fields:adapter.qq_sandbox.label",
        kind: "boolean",
      },
      {
        key: "owner_id",
        label: "fields:adapter.owner_id.label",
        kind: "text",
        help: "fields:adapter.owner_id.help",
      },
      { key: "groups", label: "fields:adapter.groups.label", kind: "list" },
      { key: "friends", label: "fields:adapter.friends.label", kind: "list" },
      {
        key: "ignore_users",
        label: "fields:adapter.ignore_users.label",
        kind: "list",
      },
    ];
  }
  if (item.type !== "feishu")
    return [...ADAPTER_COMMON_FIELDS, ...ONEBOT_FIELDS];
  return [
    ...ADAPTER_COMMON_FIELDS,
    {
      key: "domain",
      label: "fields:adapter.domain.label",
      kind: "select",
      options: ["feishu", "lark"],
      optionLabels: {
        feishu: "fields:adapter.domain.options.feishu",
        lark: "Lark",
      },
    },
    { key: "app_id", label: "App ID", kind: "text", placeholder: "cli_…" },
    {
      key: "app_secret",
      label: "App Secret",
      kind: "write-only",
      placeholder: hasSavedSecret
        ? "fields:adapter.app_secret.placeholder"
        : "fields:adapter.app_secret.placeholder3",
    },
    {
      key: "owner_id",
      label: "fields:adapter.owner_id.label2",
      kind: "text",
      placeholder: "ou_…",
      help: "fields:adapter.owner_id.help2",
    },
    {
      key: "memory_profile",
      label: "fields:adapter.memory_profile.label",
      kind: "select",
      options: ["feishu", "default"],
      optionLabels: {
        feishu: "fields:adapter.memory_profile.options.feishu",
        default: "fields:adapter.memory_profile.options.default",
      },
      help: "fields:adapter.memory_profile.help",
    },
    {
      key: "groups",
      label: "fields:adapter.groups.label2",
      kind: "list",
      help: "fields:adapter.groups.help",
    },
    {
      key: "wake_keywords",
      label: "fields:adapter.wake_keywords.label",
      kind: "list",
      help: "fields:adapter.wake_keywords.help",
    },
    {
      key: "feishu_group_context",
      label: "fields:adapter.feishu_group_context.label",
      kind: "boolean",
      help: "fields:adapter.feishu_group_context.help",
    },
  ];
}

const SUBAGENT_COMMON_FIELDS: FieldSpec[] = [
  { key: "id", label: "fields:subagentCommon.id.label", kind: "text" },
  {
    key: "enabled",
    label: "fields:subagentCommon.enabled.label",
    kind: "boolean",
    help: "fields:subagentCommon.enabled.help",
  },
  {
    key: "kind",
    label: "fields:subagentCommon.kind.label",
    kind: "select",
    options: [
      "shiritaibot",
      "codex",
      "claude_code",
      "deepseek_harness",
      "antigravity",
      "generic",
    ],
    optionLabels: {
      shiritaibot: "fields:subagentCommon.kind.options.shiritaibot",
      codex: "Codex",
      claude_code: "Claude Code",
      deepseek_harness: "DeepSeek Harness",
      antigravity: "Google Antigravity CLI",
      generic: "fields:subagentCommon.kind.options.generic",
    },
  },
  {
    key: "command",
    label: "fields:subagentCommon.command.label",
    kind: "list",
    help: "fields:subagentCommon.command.help",
  },
  {
    key: "timeout",
    label: "fields:subagentCommon.timeout.label",
    kind: "number",
    min: 5,
    max: 7200,
  },
  {
    key: "proxy",
    label: "fields:subagentCommon.proxy.label",
    kind: "text",
    placeholder: "http://127.0.0.1:7890",
    help: "fields:subagentCommon.proxy.help",
  },
  {
    key: "model",
    label: "fields:subagentCommon.model.label",
    kind: "text",
    help: "fields:subagentCommon.model.help",
  },
];

const ADD_DIRS_FIELD: FieldSpec = {
  key: "add_dirs",
  label: "fields:addDirs.add_dirs.label",
  kind: "list",
  help: "fields:addDirs.add_dirs.help",
  full: true,
};

const CODEX_FIELDS: FieldSpec[] = [
  {
    key: "sandbox",
    label: "fields:codex.sandbox.label",
    kind: "select",
    options: ["read-only", "workspace-write", "danger-full-access"],
    optionLabels: {
      "read-only": "fields:codex.sandbox.options.read_only",
      "workspace-write": "fields:codex.sandbox.options.workspace_write",
      "danger-full-access": "fields:codex.sandbox.options.danger_full_access",
    },
    help: "fields:codex.sandbox.help",
  },
  {
    key: "network_access",
    label: "fields:codex.network_access.label",
    kind: "boolean",
    help: "fields:codex.network_access.help",
  },
  {
    ...ADD_DIRS_FIELD,
    help: "fields:codex.add_dirs.help",
  },
];

const CLAUDE_FIELDS: FieldSpec[] = [
  {
    key: "permission_mode",
    label: "fields:claude.permission_mode.label",
    kind: "select",
    options: [
      "acceptEdits",
      "default",
      "dontAsk",
      "auto",
      "plan",
      "bypassPermissions",
    ],
    optionLabels: {
      acceptEdits: "fields:claude.permission_mode.options.acceptEdits",
      default: "fields:claude.permission_mode.options.default",
      dontAsk: "fields:claude.permission_mode.options.dontAsk",
      auto: "fields:claude.permission_mode.options.auto",
      plan: "fields:claude.permission_mode.options.plan",
      bypassPermissions:
        "fields:claude.permission_mode.options.bypassPermissions",
    },
    help: "fields:claude.permission_mode.help",
  },
  {
    key: "tools",
    label: "fields:claude.tools.label",
    kind: "list",
    help: "fields:claude.tools.help",
  },
  {
    key: "allowed_tools",
    label: "fields:claude.allowed_tools.label",
    kind: "list",
    help: "fields:claude.allowed_tools.help",
  },
  {
    key: "disallowed_tools",
    label: "fields:claude.disallowed_tools.label",
    kind: "list",
    help: "fields:claude.disallowed_tools.help",
  },
  ADD_DIRS_FIELD,
];

const DSH_FIELDS: FieldSpec[] = [
  {
    key: "permission_preset",
    label: "fields:dsh.permission_preset.label",
    kind: "text",
    help: "fields:dsh.permission_preset.help",
  },
  {
    key: "approval_policy",
    label: "fields:dsh.approval_policy.label",
    kind: "select",
    options: ["never", "ask"],
    optionLabels: {
      never: "fields:dsh.approval_policy.options.never",
      ask: "fields:dsh.approval_policy.options.ask",
    },
    help: "fields:dsh.approval_policy.help",
  },
  {
    ...ADD_DIRS_FIELD,
    help: "fields:dsh.add_dirs.help",
  },
];

export function subagentFields(
  item: SubagentConfig,
  models: string[],
  tools: string[],
  skills: string[],
): FieldSpec[] {
  if (item.kind === "shiritaibot")
    return [
      ...SUBAGENT_COMMON_FIELDS.filter(
        (f) => !["command", "proxy", "model"].includes(f.key),
      ),
      {
        key: "model",
        label: "fields:subagent.model.label",
        kind: "select",
        options: [...new Set(["", ...models, item.model])],
        optionLabels: { "": "fields:subagent.model.options.none" },
      },
      {
        key: "instructions",
        label: "fields:subagent.instructions.label",
        kind: "textarea",
        full: true,
        help: "fields:subagent.instructions.help",
      },
      {
        key: "native_tools",
        label: "fields:subagent.native_tools.label",
        kind: "multiselect",
        full: true,
        options: [...new Set([...tools, ...item.native_tools])],
        help: "fields:subagent.native_tools.help",
      },
      {
        key: "native_skills",
        label: "fields:subagent.native_skills.label",
        kind: "multiselect",
        full: true,
        options: [...new Set([...skills, ...item.native_skills])],
        help: "fields:subagent.native_skills.help",
      },
      {
        key: "native_manual_approval",
        label: "fields:subagent.native_manual_approval.label",
        kind: "boolean",
        help: "fields:subagent.native_manual_approval.help",
      },
    ];
  if (item.kind === "codex")
    return [...SUBAGENT_COMMON_FIELDS, ...CODEX_FIELDS];
  if (item.kind === "claude_code")
    return [...SUBAGENT_COMMON_FIELDS, ...CLAUDE_FIELDS];
  if (item.kind === "antigravity")
    return [
      ...SUBAGENT_COMMON_FIELDS,
      {
        key: "antigravity_mode",
        label: "fields:subagent.antigravity_mode.label",
        kind: "select",
        options: ["", "plan", "accept-edits", "auto"],
        optionLabels: {
          "": "fields:subagent.antigravity_mode.options.none",
          plan: "fields:subagent.antigravity_mode.options.plan",
          "accept-edits":
            "fields:subagent.antigravity_mode.options.accept_edits",
          auto: "fields:subagent.antigravity_mode.options.auto",
        },
      },
      ADD_DIRS_FIELD,
    ];
  if (item.kind === "deepseek_harness")
    // dsh has no model flag: the route comes from its own profile.
    return [
      ...SUBAGENT_COMMON_FIELDS.filter((field) => field.key !== "model"),
      ...DSH_FIELDS,
    ];
  return SUBAGENT_COMMON_FIELDS;
}

export function subagentSummary(item: SubagentConfig): string {
  if (item.kind === "shiritaibot")
    return "ShiritaiBot · " + (item.model || "Agent Work");
  if (item.kind === "codex")
    return (
      "codex · " +
      item.sandbox +
      (item.network_access ? i18n.t("fields:subagentSummary.text") : "")
    );
  if (item.kind === "claude_code")
    return "claude_code · " + item.permission_mode;
  if (item.kind === "deepseek_harness")
    return "deepseek_harness · " + item.permission_preset;
  return item.kind;
}

export const POLICY_FIELDS: FieldSpec[] = [
  {
    key: "shell_command",
    label: "fields:policy.shell_command.label",
    kind: "list",
    help: "fields:policy.shell_command.help",
  },
  {
    key: "approval_timeout",
    label: "fields:policy.approval_timeout.label",
    kind: "number",
    min: 1,
    max: 3600,
  },
  {
    key: "approval_allow_threshold",
    label: "fields:policy.approval_allow_threshold.label",
    kind: "number",
    min: 0.5,
    max: 1,
    step: 0.01,
    help: "fields:policy.approval_allow_threshold.help",
  },
  {
    key: "approval_deny_threshold",
    label: "fields:policy.approval_deny_threshold.label",
    kind: "number",
    min: 0.5,
    max: 1,
    step: 0.01,
    help: "fields:policy.approval_deny_threshold.help",
  },
  {
    key: "approval_on_deny",
    label: "fields:policy.approval_on_deny.label",
    kind: "select",
    options: ["fallback", "deny"],
    optionLabels: {
      fallback: "fields:policy.approval_on_deny.options.fallback",
      deny: "fields:policy.approval_on_deny.options.deny",
    },
  },
  {
    key: "approval_on_unsure",
    label: "fields:policy.approval_on_unsure.label",
    kind: "select",
    options: ["fallback", "deny"],
    optionLabels: {
      fallback: "fields:policy.approval_on_unsure.options.fallback",
      deny: "fields:policy.approval_on_unsure.options.deny",
    },
  },
  {
    key: "max_steps",
    label: "fields:policy.max_steps.label",
    kind: "number",
    min: 1,
    max: 100,
  },
  {
    key: "max_turn_seconds",
    label: "fields:policy.max_turn_seconds.label",
    kind: "number",
    min: 10,
    max: 7200,
  },
  {
    key: "max_context_chars",
    label: "fields:policy.max_context_chars.label",
    kind: "number",
    min: 4000,
    max: 1000000,
  },
  {
    key: "dynamic_subagent",
    label: "fields:policy.dynamic_subagent.label",
    kind: "boolean",
    help: "fields:policy.dynamic_subagent.help",
  },
  {
    key: "dynamic_subagent_model_override",
    label: "fields:policy.dynamic_subagent_model_override.label",
    kind: "boolean",
    help: "fields:policy.dynamic_subagent_model_override.help",
  },
];

export const MESSAGE_MERGE_FIELDS: FieldSpec[] = [
  {
    key: "debounce_enabled",
    label: "fields:messageMerge.debounce_enabled.label",
    kind: "boolean",
    help: "fields:messageMerge.debounce_enabled.help",
  },
  {
    key: "debounce_seconds",
    label: "fields:messageMerge.debounce_seconds.label",
    kind: "number",
    min: 0,
    max: 30,
    help: "fields:messageMerge.debounce_seconds.help",
  },
  {
    key: "debounce_max_seconds",
    label: "fields:messageMerge.debounce_max_seconds.label",
    kind: "number",
    min: 0,
    max: 60,
    help: "fields:messageMerge.debounce_max_seconds.help",
  },
  {
    key: "debounce_suffixes",
    label: "fields:messageMerge.debounce_suffixes.label",
    kind: "list",
    help: "fields:messageMerge.debounce_suffixes.help",
  },
];

export const PROACTIVE_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:proactive.enabled.label",
    kind: "boolean",
    help: "fields:proactive.enabled.help",
  },
  {
    key: "timezone",
    label: "fields:proactive.timezone.label",
    kind: "text",
    placeholder: "Asia/Shanghai",
  },
  {
    key: "quiet_start",
    label: "fields:proactive.quiet_start.label",
    kind: "time",
  },
  { key: "quiet_end", label: "fields:proactive.quiet_end.label", kind: "time" },
  {
    key: "throttle_seconds",
    label: "fields:proactive.throttle_seconds.label",
    kind: "number",
    min: 0,
    max: 86400,
  },
];

export const BASIC_SETTINGS_FIELDS: FieldSpec[] = [
  {
    key: "persona",
    label: "fields:basicSettings.persona.label",
    kind: "textarea",
    full: true,
    help: "fields:basicSettings.persona.help",
  },
  {
    key: "concurrency",
    label: "fields:basicSettings.concurrency.label",
    kind: "number",
    min: 1,
    max: 32,
    help: "fields:basicSettings.concurrency.help",
  },
];

/** Catalog keys rather than text: FieldForm translates them (docs/I18N.md). */
export const LANGUAGE_FIELDS: FieldSpec[] = [
  {
    key: "locale",
    label: "settings:language.botLabel",
    kind: "select",
    options: ["zh-CN", "en"],
    optionLabels: LOCALE_NAMES,
    help: "settings:language.botHelp",
  },
];

export const IDENTITY_FIELDS: FieldSpec[] = [
  {
    key: "assistant_name",
    label: "fields:identity.assistant_name.label",
    kind: "text",
    help: "fields:identity.assistant_name.help",
  },
  {
    key: "owner_name",
    label: "fields:identity.owner_name.label",
    kind: "text",
  },
  {
    key: "wake_keywords",
    label: "fields:identity.wake_keywords.label",
    kind: "list",
    help: "fields:identity.wake_keywords.help",
  },
  {
    key: "wake_keyword_mode",
    label: "fields:identity.wake_keyword_mode.label",
    kind: "select",
    options: ["contains", "prefix"],
    optionLabels: {
      contains: "fields:identity.wake_keyword_mode.options.contains",
      prefix: "fields:identity.wake_keyword_mode.options.prefix",
    },
    help: "fields:identity.wake_keyword_mode.help",
  },
  {
    key: "group_command_require_wake",
    label: "fields:identity.group_command_require_wake.label",
    kind: "boolean",
    help: "fields:identity.group_command_require_wake.help",
  },
  {
    key: "private_require_keyword",
    label: "fields:identity.private_require_keyword.label",
    kind: "boolean",
    help: "fields:identity.private_require_keyword.help",
  },
];

export const OUTBOUND_MESSAGE_FIELDS: FieldSpec[] = [
  {
    key: "help_menu_style",
    label: "fields:outboundMessage.help_menu_style.label",
    kind: "select",
    options: ["text", "light", "dark"],
    optionLabels: {
      text: "fields:outboundMessage.help_menu_style.options.text",
      light: "fields:outboundMessage.help_menu_style.options.light",
      dark: "fields:outboundMessage.help_menu_style.options.dark",
    },
    help: "fields:outboundMessage.help_menu_style.help",
  },
  {
    key: "onebot_forward_long_messages",
    label: "fields:outboundMessage.onebot_forward_long_messages.label",
    kind: "boolean",
    help: "fields:outboundMessage.onebot_forward_long_messages.help",
  },
  {
    key: "onebot_forward_threshold",
    label: "fields:outboundMessage.onebot_forward_threshold.label",
    kind: "number",
    min: 1,
    help: "fields:outboundMessage.onebot_forward_threshold.help",
  },
];

export const PERSONA_FIELDS: FieldSpec[] = [
  {
    key: "id",
    label: "fields:persona.id.label",
    kind: "text",
    help: "fields:persona.id.help",
  },
  { key: "name", label: "fields:persona.name.label", kind: "text" },
  {
    key: "enabled",
    label: "fields:persona.enabled.label",
    kind: "boolean",
    help: "fields:persona.enabled.help",
  },
  {
    key: "prompt",
    label: "fields:persona.prompt.label",
    kind: "textarea",
    full: true,
    help: "fields:persona.prompt.help",
  },
];

export const CONTEXT_FIELDS: FieldSpec[] = [
  {
    key: "compression_tool_chars",
    label: "fields:context.compression_tool_chars.label",
    kind: "number",
    min: 0,
    max: 1000000,
    help: "fields:context.compression_tool_chars.help",
  },
  {
    key: "compression_failure",
    label: "fields:context.compression_failure.label",
    kind: "select",
    options: ["discard", "error"],
    optionLabels: {
      discard: "fields:context.compression_failure.options.discard",
      error: "fields:context.compression_failure.options.error",
    },
  },
  {
    key: "max_turns",
    label: "fields:context.max_turns.label",
    kind: "number",
    min: 1,
    max: 10000,
    help: "fields:context.max_turns.help",
  },
  {
    key: "keep_recent_turns",
    label: "fields:context.keep_recent_turns.label",
    kind: "number",
    min: 0,
    max: 9999,
    help: "fields:context.keep_recent_turns.help",
  },
];

export const MEMORY_FIELDS: FieldSpec[] = [
  {
    key: "inject",
    label: "fields:memory.inject.label",
    kind: "boolean",
    help: "fields:memory.inject.help",
  },
  {
    key: "inject_chars",
    label: "fields:memory.inject_chars.label",
    kind: "number",
    min: 200,
    max: 20000,
    help: "fields:memory.inject_chars.help",
  },
  {
    key: "extract",
    label: "fields:memory.extract.label",
    kind: "boolean",
    help: "fields:memory.extract.help",
  },
  {
    key: "extract_messages",
    label: "fields:memory.extract_messages.label",
    kind: "number",
    min: 2,
    max: 200,
  },
  {
    key: "extract_idle_minutes",
    label: "fields:memory.extract_idle_minutes.label",
    kind: "number",
    min: 1,
    max: 1440,
    help: "fields:memory.extract_idle_minutes.help",
  },
];

export const GROUP_CONTEXT_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:groupContext.enabled.label",
    kind: "boolean",
    help: "fields:groupContext.enabled.help",
  },
  {
    key: "inject_messages",
    label: "fields:groupContext.inject_messages.label",
    kind: "number",
    min: 0,
    max: 200,
    help: "fields:groupContext.inject_messages.help",
  },
  {
    key: "inject_chars",
    label: "fields:groupContext.inject_chars.label",
    kind: "number",
    min: 200,
    max: 100000,
    help: "fields:groupContext.inject_chars.help",
  },
  {
    key: "query_tool",
    label: "fields:groupContext.query_tool.label",
    kind: "boolean",
    help: "fields:groupContext.query_tool.help",
  },
  {
    key: "interject_mode",
    label: "fields:groupContext.interject_mode.label",
    kind: "select",
    options: ["off", "simple", "heartflow"],
    optionLabels: {
      off: "fields:groupContext.interject_mode.options.off",
      simple: "fields:groupContext.interject_mode.options.simple",
      heartflow: "fields:groupContext.interject_mode.options.heartflow",
    },
    help: "fields:groupContext.interject_mode.help",
  },
];

export const SIMPLE_INTERJECT_FIELDS: FieldSpec[] = [
  {
    key: "interject_probability",
    label: "fields:simpleInterject.interject_probability.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.01,
    help: "fields:simpleInterject.interject_probability.help",
  },
  {
    key: "interject_cooldown",
    label: "fields:simpleInterject.interject_cooldown.label",
    kind: "number",
    min: 60,
    max: 86400,
    help: "fields:simpleInterject.interject_cooldown.help",
  },
];

export const INTERJECT_LIMIT_FIELD: FieldSpec = {
  key: "interject_max_per_hour",
  label: "fields:interjectLimit.interject_max_per_hour.label",
  kind: "number",
  min: 1,
  max: 60,
  help: "fields:interjectLimit.interject_max_per_hour.help",
};

export const HEARTFLOW_FIELDS: FieldSpec[] = [
  {
    key: "activity",
    label: "fields:heartflow.activity.label",
    kind: "slider",
    min: 0,
    max: 1,
    step: 0.05,
  },
  {
    key: "focus_seconds",
    label: "fields:heartflow.focus_seconds.label",
    kind: "number",
    min: 0,
    max: 3600,
    help: "fields:heartflow.focus_seconds.help",
  },
  {
    key: "max_streak",
    label: "fields:heartflow.max_streak.label",
    kind: "number",
    min: 1,
    max: 20,
  },
];

export const HEARTFLOW_ADVANCED_FIELDS: FieldSpec[] = [
  {
    key: "rest_seconds",
    label: "fields:heartflowAdvanced.rest_seconds.label",
    kind: "number",
    min: 1,
    max: 86400,
  },
  {
    key: "energy_curve",
    label: "fields:heartflowAdvanced.energy_curve.label",
    kind: "select",
    options: ["flat", "linear", "exponential"],
    optionLabels: {
      flat: "fields:heartflowAdvanced.energy_curve.options.flat",
      linear: "fields:heartflowAdvanced.energy_curve.options.linear",
      exponential: "fields:heartflowAdvanced.energy_curve.options.exponential",
    },
  },
  {
    key: "energy_recovery_minutes",
    label: "fields:heartflowAdvanced.energy_recovery_minutes.label",
    kind: "number",
    min: 1,
    max: 1440,
  },
  {
    key: "debounce_seconds",
    label: "fields:heartflowAdvanced.debounce_seconds.label",
    kind: "number",
    min: 0.1,
    max: 12,
    step: 0.1,
  },
  {
    key: "min_judge_interval",
    label: "fields:heartflowAdvanced.min_judge_interval.label",
    kind: "number",
    min: 1,
    max: 3600,
  },
  {
    key: "developer",
    label: "fields:heartflowAdvanced.developer.label",
    kind: "boolean",
  },
];

export const VISION_FIELDS: FieldSpec[] = [
  {
    key: "caption",
    label: "fields:vision.caption.label",
    kind: "boolean",
    help: "fields:vision.caption.help",
  },
  {
    key: "max_images",
    label: "fields:vision.max_images.label",
    kind: "number",
    min: 0,
    max: 20,
    help: "fields:vision.max_images.help",
  },
];

export const KNOWLEDGE_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:knowledge.enabled.label",
    kind: "boolean",
    help: "fields:knowledge.enabled.help",
  },
  {
    key: "chat",
    label: "fields:knowledge.chat.label",
    kind: "boolean",
    help: "fields:knowledge.chat.help",
  },
  {
    key: "chunk_chars",
    label: "fields:knowledge.chunk_chars.label",
    kind: "number",
    min: 200,
    max: 2000,
    help: "fields:knowledge.chunk_chars.help",
  },
  {
    key: "chunk_overlap",
    label: "fields:knowledge.chunk_overlap.label",
    kind: "number",
    min: 0,
    max: 500,
    help: "fields:knowledge.chunk_overlap.help",
  },
  {
    key: "max_file_mb",
    label: "fields:knowledge.max_file_mb.label",
    kind: "number",
    min: 1,
    max: 50,
    help: "fields:knowledge.max_file_mb.help",
  },
];

export const MEME_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:meme.enabled.label",
    kind: "boolean",
    help: "fields:meme.enabled.help",
  },
  {
    key: "capacity",
    label: "fields:meme.capacity.label",
    kind: "number",
    min: 1,
    max: 10000,
    help: "fields:meme.capacity.help",
  },
  {
    key: "inject",
    label: "fields:meme.inject.label",
    kind: "boolean",
    help: "fields:meme.inject.help",
  },
  {
    key: "max_per_reply",
    label: "fields:meme.max_per_reply.label",
    kind: "number",
    min: 1,
    max: 10,
    help: "fields:meme.max_per_reply.help",
  },
  {
    key: "collect",
    label: "fields:meme.collect.label",
    kind: "boolean",
    help: "fields:meme.collect.help",
  },
  {
    key: "collect_rate",
    label: "fields:meme.collect_rate.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.01,
    help: "fields:meme.collect_rate.help",
  },
  {
    key: "stickers_only",
    label: "fields:meme.stickers_only.label",
    kind: "boolean",
    help: "fields:meme.stickers_only.help",
  },
  {
    key: "persona_check",
    label: "fields:meme.persona_check.label",
    kind: "boolean",
    help: "fields:meme.persona_check.help",
  },
  {
    key: "fit_threshold",
    label: "fields:meme.fit_threshold.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.01,
    help: "fields:meme.fit_threshold.help",
  },
];

export const EMOTION_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:emotion.enabled.label",
    kind: "boolean",
    help: "fields:emotion.enabled.help",
  },
  {
    key: "sensitivity",
    label: "fields:emotion.sensitivity.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.sensitivity.help",
  },
  {
    key: "tone",
    label: "fields:emotion.tone.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.tone.help",
  },
  {
    key: "initiative",
    label: "fields:emotion.initiative.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.initiative.help",
  },
  {
    key: "persistence",
    label: "fields:emotion.persistence.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.persistence.help",
  },
  {
    key: "judge_weight",
    label: "fields:emotion.judge_weight.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.judge_weight.help",
  },
  {
    key: "rules_weight",
    label: "fields:emotion.rules_weight.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.rules_weight.help",
  },
  {
    key: "sentiment_weight",
    label: "fields:emotion.sentiment_weight.label",
    kind: "number",
    min: 0,
    max: 1,
    step: 0.05,
    help: "fields:emotion.sentiment_weight.help",
  },
  {
    key: "developer",
    label: "fields:emotion.developer.label",
    kind: "boolean",
    help: "fields:emotion.developer.help",
  },
];

export function createPersona(rows: PersonaConfig[]): PersonaConfig {
  return {
    id: uniqueId("persona-new", rows),
    enabled: true,
    name: i18n.t("fields:createPersona.name"),
    prompt: i18n.t("fields:createPersona.prompt"),
  };
}

/** Fallback for plugin settings the frontend does not know about yet. */
export function inferFieldSpec(key: string, value: unknown): FieldSpec {
  if (typeof value === "boolean") return { key, label: key, kind: "boolean" };
  if (typeof value === "number") return { key, label: key, kind: "number" };
  if (Array.isArray(value)) return { key, label: key, kind: "list" };
  return { key, label: key, kind: "text" };
}

export function createProvider(
  rows: ProviderConfig[],
  preset: ProviderPreset = PROVIDER_PRESETS[0],
): ProviderConfig {
  return {
    id: uniqueId(preset.id, rows),
    enabled: true,
    protocol: preset.protocol,
    base_url: preset.base_url,
    api_key: "",
    timeout: 120,
    max_retries: 2,
    proxy: "",
  };
}

export function createAdapter(
  rows: AdapterConfig[],
  type: NonNullable<AdapterConfig["type"]> = "onebot11",
): AdapterConfig {
  return {
    id: uniqueId(
      type === "onebot11" ? "qq-main" : type.replace("_", "-"),
      rows,
    ),
    enabled: false,
    type,
    url: "ws://127.0.0.1:3001",
    token: "",
    self_id: "",
    owner_id: "",
    groups: [],
    friends: [],
    ignore_users: [],
    auto_start: [],
    auto_start_after: 60,
    domain: "feishu",
    wake_keywords: ["小知"],
    feishu_group_context: false,
    memory_profile: "feishu",
  };
}

export function createSubagent(rows: SubagentConfig[]): SubagentConfig {
  return {
    id: uniqueId("codex-main", rows),
    enabled: false,
    kind: "codex",
    command: ["codex"],
    instructions: "",
    native_tools: [],
    native_skills: [],
    native_manual_approval: false,
    timeout: 900,
    model: "",
    proxy: "",
    antigravity_mode: "",
    sandbox: "workspace-write",
    network_access: false,
    permission_mode: "acceptEdits",
    tools: [],
    allowed_tools: ["Bash", "WebFetch", "WebSearch"],
    disallowed_tools: [],
    permission_preset: "workspace-write",
    approval_policy: "never",
    add_dirs: [],
  };
}

export const WEB_SEARCH_FIELDS: FieldSpec[] = [
  {
    key: "enabled",
    label: "fields:webSearch.enabled.label",
    kind: "boolean",
    help: "fields:webSearch.enabled.help",
  },
  {
    key: "provider",
    label: "fields:webSearch.provider.label",
    kind: "select",
    options: ["tavily", "serpapi", "deepseek"],
    optionLabels: {
      tavily: "Tavily",
      serpapi: "SerpAPI",
      deepseek: "DeepSeek",
    },
  },
  {
    key: "max_results",
    label: "fields:webSearch.max_results.label",
    kind: "number",
    min: 1,
    max: 10,
    help: "fields:webSearch.max_results.help",
  },
  {
    key: "timeout",
    label: "fields:webSearch.timeout.label",
    kind: "number",
    min: 5,
    max: 120,
  },
];

export const WEB_SEARCH_PROVIDER_FIELDS: Record<string, FieldSpec[]> = {
  tavily: [
    { key: "api_key", label: "Tavily API Key", kind: "secret" },
    {
      key: "search_depth",
      label: "fields:webSearchProvider.search_depth.label",
      kind: "select",
      options: ["basic", "advanced", "fast", "ultra-fast"],
      optionLabels: {
        basic: "fields:webSearchProvider.search_depth.options.basic",
        advanced: "fields:webSearchProvider.search_depth.options.advanced",
        fast: "fields:webSearchProvider.search_depth.options.fast",
        "ultra-fast":
          "fields:webSearchProvider.search_depth.options.ultra_fast",
      },
    },
    {
      key: "topic",
      label: "fields:webSearchProvider.topic.label",
      kind: "select",
      options: ["general", "news", "finance"],
      optionLabels: {
        general: "fields:webSearchProvider.topic.options.general",
        news: "fields:webSearchProvider.topic.options.news",
        finance: "fields:webSearchProvider.topic.options.finance",
      },
    },
  ],
  serpapi: [
    { key: "api_key", label: "SerpAPI API Key", kind: "secret" },
    {
      key: "hl",
      label: "fields:webSearchProvider.hl.label",
      kind: "text",
      help: "fields:webSearchProvider.hl.help",
    },
    {
      key: "gl",
      label: "fields:webSearchProvider.gl.label",
      kind: "text",
      help: "fields:webSearchProvider.gl.help",
    },
  ],
  deepseek: [
    { key: "api_key", label: "DeepSeek API Key", kind: "secret" },
    {
      key: "need_content",
      label: "fields:webSearchProvider.need_content.label",
      kind: "boolean",
      help: "fields:webSearchProvider.need_content.help",
    },
    {
      key: "localization",
      label: "fields:webSearchProvider.localization.label",
      kind: "text",
      help: "fields:webSearchProvider.localization.help",
    },
  ],
};

const MCP_COMMON_FIELDS: FieldSpec[] = [
  {
    key: "id",
    label: "fields:mcpCommon.id.label",
    kind: "text",
    help: "fields:mcpCommon.id.help",
  },
  {
    key: "enabled",
    label: "fields:mcpCommon.enabled.label",
    kind: "boolean",
    help: "fields:mcpCommon.enabled.help",
  },
  {
    key: "transport",
    label: "fields:mcpCommon.transport.label",
    kind: "select",
    options: ["stdio", "http"],
    optionLabels: {
      stdio: "fields:mcpCommon.transport.options.stdio",
      http: "fields:mcpCommon.transport.options.http",
    },
  },
];

const MCP_STDIO_FIELDS: FieldSpec[] = [
  {
    key: "command",
    label: "fields:mcpStdio.command.label",
    kind: "list",
    placeholder:
      "npx\n-y\n@modelcontextprotocol/server-filesystem\n/home/me/docs",
    help: "fields:mcpStdio.command.help",
    full: true,
  },
  {
    key: "env",
    label: "fields:mcpStdio.env.label",
    kind: "json",
    placeholder: '{"API_KEY": "env:MY_API_KEY"}',
    help: "fields:mcpStdio.env.help",
    full: true,
  },
  {
    key: "cwd",
    label: "fields:mcpStdio.cwd.label",
    kind: "text",
    placeholder: "fields:mcpStdio.cwd.placeholder",
    help: "fields:mcpStdio.cwd.help",
  },
];

const MCP_HTTP_FIELDS: FieldSpec[] = [
  {
    key: "url",
    label: "fields:mcpHttp.url.label",
    kind: "text",
    placeholder: "https://example.com/mcp",
    help: "fields:mcpHttp.url.help",
    full: true,
  },
  {
    key: "headers",
    label: "fields:mcpHttp.headers.label",
    kind: "json",
    placeholder: '{"Authorization": "Bearer ..."}',
    help: "fields:mcpHttp.headers.help",
    full: true,
  },
];

const MCP_POLICY_FIELDS: FieldSpec[] = [
  {
    key: "timeout",
    label: "fields:mcpPolicy.timeout.label",
    kind: "number",
    min: 5,
    max: 600,
  },
  {
    key: "risk",
    label: "fields:mcpPolicy.risk.label",
    kind: "select",
    options: ["safe", "normal", "sensitive", "dangerous"],
    optionLabels: {
      safe: "fields:mcpPolicy.risk.options.safe",
      normal: "normal",
      sensitive: "sensitive",
      dangerous: "dangerous",
    },
    help: "fields:mcpPolicy.risk.help",
  },
  {
    key: "tool_risks",
    label: "fields:mcpPolicy.tool_risks.label",
    kind: "json",
    placeholder: '{"read_file": "safe", "write_file": "dangerous"}',
    help: "fields:mcpPolicy.tool_risks.help",
    full: true,
  },
  {
    key: "disabled_tools",
    label: "fields:mcpPolicy.disabled_tools.label",
    kind: "list",
    placeholder: "fields:mcpPolicy.disabled_tools.placeholder",
    help: "fields:mcpPolicy.disabled_tools.help",
    full: true,
  },
];

export function mcpFields(item: McpServerConfig): FieldSpec[] {
  return [
    ...MCP_COMMON_FIELDS,
    ...(item.transport === "http" ? MCP_HTTP_FIELDS : MCP_STDIO_FIELDS),
    ...MCP_POLICY_FIELDS,
  ];
}

export function mcpSummary(item: McpServerConfig): string {
  return item.transport === "http"
    ? "http · " + (item.url || i18n.t("fields:mcpSummary.text"))
    : "stdio · " + (item.command[0] || i18n.t("fields:mcpSummary.text2"));
}

export function createMcpServer(rows: McpServerConfig[]): McpServerConfig {
  return {
    id: uniqueId("mcp", rows),
    enabled: false,
    transport: "stdio",
    command: [],
    env: {},
    cwd: "",
    url: "",
    headers: {},
    timeout: 60,
    risk: "sensitive",
    tool_risks: {},
    disabled_tools: [],
  };
}
