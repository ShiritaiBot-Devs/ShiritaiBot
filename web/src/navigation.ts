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

/** A sidebar page. Its name and description are `shell:pages.<id>.label` / `.subtitle`. */
export interface PageMeta {
  id: NavPageId;
  icon: LucideIcon;
}

/** Pages with their own sidebar entry; the capability tabs live under "approvals". */
export type NavPageId = Exclude<PageId, "mcp" | "skills">;

/** Tabs of the capabilities page; labels are `shell:capabilities.<id>`. */
export const CAPABILITY_TABS = [
  { id: "approvals" },
  { id: "mcp" },
  { id: "skills" },
] as const;

/** Sidebar groups; titles are `shell:nav.<id>`. */
export const NAV_GROUPS: { id: "features" | "config"; pages: PageMeta[] }[] = [
  {
    id: "features",
    pages: [
      { id: "overview", icon: LayoutDashboard },
      { id: "chat", icon: MessageSquare },
      { id: "routine", icon: CalendarDays },
      { id: "events", icon: Activity },
      { id: "logs", icon: Terminal },
    ],
  },
  {
    id: "config",
    pages: [
      { id: "models", icon: Bot },
      { id: "memes", icon: Smile },
      { id: "subagents", icon: Workflow },
      { id: "plugins", icon: Puzzle },
      { id: "approvals", icon: ShieldCheck },
      { id: "memory", icon: Database },
      { id: "knowledge", icon: BookOpen },
      { id: "adapters", icon: Network },
      { id: "settings", icon: Settings },
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
