import {
  Activity,
  BookOpen,
  Bot,
  CalendarDays,
  Database,
  LayoutDashboard,
  MessageSquare,
  Network,
  Puzzle,
  Settings,
  ShieldCheck,
  Smile,
  Terminal,
  Workflow,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";

export type PageId =
  | "overview"
  | "chat"
  | "events"
  | "routine"
  | "memory"
  | "memes"
  | "knowledge"
  | "subagents"
  | "plugins"
  | "skills"
  | "mcp"
  | "models"
  | "adapters"
  | "approvals"
  | "logs"
  | "settings";

export interface PageMeta {
  id: PageId;
  label: string;
  subtitle: string;
  icon: LucideIcon;
}

export const CAPABILITY_TABS = [
  { id: "approvals", label: "工具" },
  { id: "mcp", label: "MCP" },
  { id: "skills", label: "技能" },
] as const;

export const NAV_GROUPS: { label: string; pages: PageMeta[] }[] = [
  {
    label: "功能",
    pages: [
      {
        id: "overview",
        label: "概览",
        subtitle: "运行状态、数据统计与最近动态。",
        icon: LayoutDashboard,
      },
      {
        id: "chat",
        label: "对话",
        subtitle: "直接和助手对话，或输入 / 使用命令。",
        icon: MessageSquare,
      },
      {
        id: "routine",
        label: "日程任务",
        subtitle: "待办、日程、提醒和定时任务。",
        icon: CalendarDays,
      },
      {
        id: "events",
        label: "主动动态",
        subtitle: "主动性引擎收到的事件与分诊结果。",
        icon: Activity,
      },
      {
        id: "logs",
        label: "活动日志",
        subtitle: "活动日志，按时间倒序。",
        icon: Terminal,
      },
    ],
  },
  {
    label: "配置",
    pages: [
      {
        id: "models",
        label: "模型与服务",
        subtitle: "LLM 提供商、模型与路由。",
        icon: Bot,
      },
      {
        id: "memes",
        label: "表情管理",
        subtitle: "表情库、分类、自动收录与使用设置。",
        icon: Smile,
      },
      {
        id: "subagents",
        label: "子代理",
        subtitle: "外部 Agent CLI 实例与任务记录。",
        icon: Workflow,
      },
      {
        id: "plugins",
        label: "插件",
        subtitle: "插件启停、配置和重载。",
        icon: Puzzle,
      },
      {
        id: "approvals",
        label: "能力与权限",
        subtitle: "管理工具权限、审批记录、MCP 服务和技能。",
        icon: ShieldCheck,
      },
      {
        id: "memory",
        label: "长期记忆",
        subtitle: "助手明确记住的长期记忆。",
        icon: Database,
      },
      {
        id: "knowledge",
        label: "知识库",
        subtitle: "存放不会变化的资料，供助手按关键词和语义检索。",
        icon: BookOpen,
      },
      {
        id: "adapters",
        label: "协议连接",
        subtitle: "管理 QQ 和飞书连接。",
        icon: Network,
      },
      {
        id: "settings",
        label: "设置",
        subtitle: "管理ShiritaiBot的各项设置。",
        icon: Settings,
      },
    ],
  },
];

const pages = NAV_GROUPS.flatMap((group) => group.pages);
const PAGE_IDS = new Set<string>([
  ...pages.map((page) => page.id),
  ...CAPABILITY_TABS.map((tab) => tab.id),
]);

export function pageFromHash(hash: string): PageId {
  const id = hash.replace(/^#\/?/, "").split(/[/?]/)[0];
  return PAGE_IDS.has(id) ? (id as PageId) : "overview";
}

export function hashForPage(id: PageId): string {
  return "#/" + id;
}

export function pageMeta(id: PageId): PageMeta {
  const parent = CAPABILITY_TABS.some((tab) => tab.id === id)
    ? "approvals"
    : id;
  return pages.find((page) => page.id === parent) ?? pages[0];
}
