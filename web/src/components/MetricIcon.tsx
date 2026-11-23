import {
  Bot,
  Wrench,
  ShieldCheck,
  Workflow,
  Coins,
  DatabaseZap,
  Wallet,
  MessagesSquare,
  Cpu,
  CircleAlert,
  type LucideIcon,
} from "lucide-react";

/** Metric cards on the overview and dashboard, by stable ID (labels are translated). */
const ICONS = {
  adapters: Bot,
  tools: Wrench,
  approvals: ShieldCheck,
  subagents: Workflow,
  tokens: Coins,
  cacheRate: DatabaseZap,
  cost: Wallet,
  messages: MessagesSquare,
  llmCalls: Cpu,
  failedTurns: CircleAlert,
} satisfies Record<string, LucideIcon>;

export type MetricId = keyof typeof ICONS;

export function MetricIcon({ id }: { id: MetricId }) {
  const Icon = ICONS[id];
  return <Icon className="metric-icon" aria-hidden="true" strokeWidth={1.5} />;
}
