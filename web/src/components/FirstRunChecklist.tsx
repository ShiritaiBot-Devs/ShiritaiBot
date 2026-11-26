import { ArrowUpRight, CheckCircle2, Circle } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Settings } from "../api/types";
import type { PageId } from "../navigation";

export function FirstRunChecklist({
  config,
  onNavigate,
  compact = false,
}: {
  config: Settings;
  onNavigate: (page: PageId) => void;
  compact?: boolean;
}) {
  const { t } = useTranslation("overview");
  const steps: { label: string; done: boolean; page: PageId }[] = [
    {
      label: t("setup.provider"),
      done: config.providers.some((provider) => provider.enabled),
      page: "models",
    },
    {
      label: t("setup.model"),
      done: config.models.some((model) => model.enabled),
      page: "models",
    },
    {
      label: t("setup.routes"),
      done: ["chat", "agent", "agent_chat"].every(
        (route) => (config.routes[route] ?? []).length > 0,
      ),
      page: "models",
    },
  ];
  if (!compact) {
    steps.push(
      {
        label: t("setup.webSearch"),
        done:
          !config.web_search.enabled ||
          Boolean(config.web_search[config.web_search.provider].api_key),
        page: "settings",
      },
      {
        label: t("setup.adapter"),
        done: config.adapters.some((adapter) => adapter.enabled),
        page: "adapters",
      },
    );
  }
  if (steps.every((step) => step.done)) return null;
  return (
    <section className={compact ? "chat-setup" : "panel"}>
      <h2>{t("setup.title")}</h2>
      {!compact && <p className="muted">{t("setup.hint")}</p>}
      <ul className="checklist">
        {steps.map((step) => (
          <li key={step.label} className={step.done ? "done" : undefined}>
            {step.done ? (
              <CheckCircle2 size={16} aria-label={t("setup.done")} />
            ) : (
              <Circle size={16} aria-label={t("setup.todo")} />
            )}
            <span className={step.done ? "muted" : ""}>{step.label}</span>
            {!step.done && (
              <button
                type="button"
                className="text-button"
                aria-label={`${t("setup.go")} · ${step.label}`}
                onClick={() => onNavigate(step.page)}
              >
                {t("setup.go")}
                <ArrowUpRight size={14} />
              </button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
