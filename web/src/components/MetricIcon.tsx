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

const ICONS: Record<string, LucideIcon> = {
  协议连接: Bot,
  可用工具: Wrench,
  等待审批: ShieldCheck,
  子代理任务: Workflow,
  "总 tokens": Coins,
  缓存命中率: DatabaseZap,
  预估花费: Wallet,
  对话消息: MessagesSquare,
  模型调用: Cpu,
  失败轮次: CircleAlert,
};

export function MetricIcon({ label }: { label: string }) {
  const Icon = ICONS[label];
  return Icon ? (
    <Icon className="metric-icon" aria-hidden="true" strokeWidth={1.5} />
  ) : null;
}
