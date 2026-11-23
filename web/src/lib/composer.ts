import { PERMISSION_PRESETS } from "./permissions";
import type { PermissionPreset } from "../api/types";
import i18n from "../i18n";

export type SendMode = "steer" | "queue";

export interface ComposerOption<T extends string> {
  value: T;
  label: string;
  hint: string;
}

// Getters, so labels follow the interface language when it changes.
export const SEND_MODES: ComposerOption<SendMode>[] = (
  ["steer", "queue"] as const
).map((value) => ({
  value,
  get label() {
    return i18n.t(`composer:sendModes.${value}.label`);
  },
  get hint() {
    return i18n.t(`composer:sendModes.${value}.hint`);
  },
}));

export type Preset = PermissionPreset;

export const PRESETS: ComposerOption<Preset>[] = PERMISSION_PRESETS.map(
  (item) => ({
    value: item.value,
    get label() {
      return item.shortLabel;
    },
    get hint() {
      return item.hint;
    },
  }),
);

const DRAFT_PREFIX = "shiritaibot-draft:";
const MODEL_PREFIX = "shiritaibot-model:";
const SEND_MODE_KEY = "shiritaibot-send-mode";

export function draftKey(session: string): string {
  return DRAFT_PREFIX + session;
}

export function modelKey(session: string): string {
  return MODEL_PREFIX + session;
}

export function readValue(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function writeValue(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Private browsing or blocked storage keeps the draft in memory only.
  }
}

export function readSendMode(): SendMode {
  return readValue(SEND_MODE_KEY) === "queue" ? "queue" : "steer";
}

export function writeSendMode(mode: SendMode): void {
  writeValue(SEND_MODE_KEY, mode === "queue" ? "queue" : "");
}

export function composerHeight(scrollHeight: number, max = 240): number {
  return Math.max(42, Math.min(scrollHeight, max));
}

export function stepIndex(
  current: number,
  delta: number,
  length: number,
): number {
  if (length <= 0) return -1;
  return (current + delta + length) % length;
}
