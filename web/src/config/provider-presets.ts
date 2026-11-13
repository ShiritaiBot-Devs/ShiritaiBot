import type { Protocol } from "../api/types";

export interface ProviderPreset {
  id: string;
  label: string;
  protocol: Protocol;
  base_url: string;
}

export const PROVIDER_PRESETS: ProviderPreset[] = [
  {
    id: "openai-compatible",
    label: "OpenAI兼容(Completions)",
    protocol: "chat_completions",
    base_url: "",
  },
  {
    id: "responses-compatible",
    label: "OpenAI兼容(Responses)",
    protocol: "responses",
    base_url: "",
  },
  {
    id: "anthropic-compatible",
    label: "Anthropic兼容",
    protocol: "anthropic",
    base_url: "",
  },
  { id: "systemone", label: "SystemOne", protocol: "systemone", base_url: "" },
  {
    id: "openai",
    label: "OpenAI API",
    protocol: "responses",
    base_url: "https://api.openai.com/v1",
  },
  {
    id: "anthropic",
    label: "Anthropic API",
    protocol: "anthropic",
    base_url: "https://api.anthropic.com/v1",
  },
  {
    id: "gemini",
    label: "Gemini API",
    protocol: "gemini",
    base_url: "https://generativelanguage.googleapis.com/v1beta",
  },
  {
    id: "gemini-completions",
    label: "Gemini Completions",
    protocol: "chat_completions",
    base_url: "https://generativelanguage.googleapis.com/v1beta/openai",
  },
  {
    id: "xai",
    label: "Xai Responses",
    protocol: "responses",
    base_url: "https://api.x.ai/v1",
  },
  {
    id: "deepseek",
    label: "Deepseek",
    protocol: "chat_completions",
    base_url: "https://api.deepseek.com",
  },
  {
    id: "zai",
    label: "Z.ai",
    protocol: "chat_completions",
    base_url: "https://api.z.ai/api/paas/v4",
  },
  {
    id: "paratera",
    label: "并行智算云",
    protocol: "chat_completions",
    base_url: "https://llmapi.paratera.com",
  },
];
