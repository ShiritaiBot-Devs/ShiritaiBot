/**
 * The zh-CN catalogs, imported statically so `t()` keys are type-checked
 * (see i18n/i18next.d.ts). Add a line here for each new namespace; the runtime
 * loads every `locales/<locale>/<namespace>.json` on its own.
 */
import adapters from "./zh-CN/adapters.json";
import approvals from "./zh-CN/approvals.json";
import charts from "./zh-CN/charts.json";
import chat from "./zh-CN/chat.json";
import common from "./zh-CN/common.json";
import composer from "./zh-CN/composer.json";
import dashboard from "./zh-CN/dashboard.json";
import events from "./zh-CN/events.json";
import feishu from "./zh-CN/feishu.json";
import fields from "./zh-CN/fields.json";
import knowledge from "./zh-CN/knowledge.json";
import logs from "./zh-CN/logs.json";
import market from "./zh-CN/market.json";
import mcp from "./zh-CN/mcp.json";
import memes from "./zh-CN/memes.json";
import memory from "./zh-CN/memory.json";
import models from "./zh-CN/models.json";
import overview from "./zh-CN/overview.json";
import permissions from "./zh-CN/permissions.json";
import plugins from "./zh-CN/plugins.json";
import routine from "./zh-CN/routine.json";
import sessions from "./zh-CN/sessions.json";
import settings from "./zh-CN/settings.json";
import shell from "./zh-CN/shell.json";
import skills from "./zh-CN/skills.json";
import subagents from "./zh-CN/subagents.json";
import subscription from "./zh-CN/subscription.json";
import update from "./zh-CN/update.json";

export const reference = {
  adapters,
  approvals,
  charts,
  chat,
  common,
  composer,
  dashboard,
  events,
  feishu,
  fields,
  knowledge,
  logs,
  market,
  mcp,
  memes,
  memory,
  models,
  overview,
  permissions,
  plugins,
  routine,
  sessions,
  settings,
  shell,
  skills,
  subagents,
  subscription,
  update,
};
export type Namespace = keyof typeof reference;
