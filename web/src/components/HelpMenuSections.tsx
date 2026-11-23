import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { HelpMenuSectionSetting } from "../api/types";
import i18n from "../i18n";

export function HelpMenuSections({
  client,
  value,
  onChange,
}: {
  client: ApiClient;
  value: HelpMenuSectionSetting[];
  onChange: (next: HelpMenuSectionSetting[]) => void;
}) {
  const catalog = useQuery({
    queryKey: ["help-menu-sections"],
    queryFn: () => client.helpMenuSections(),
  });
  const titles = new Map(catalog.data?.map(({ id, title }) => [id, title]));
  const settings = new Map(value.map((section) => [section.id, section]));
  for (const id of titles.keys()) {
    if (!settings.has(id)) settings.set(id, { id, visible: true });
  }
  const order = [...settings.values()];
  const rows = order.flatMap((section, index) => {
    const title = titles.get(section.id);
    return title === undefined ? [] : [{ ...section, title, index }];
  });

  function move(from: number, to: number) {
    const next = [...order];
    [next[from], next[to]] = [next[to], next[from]];
    onChange(next);
  }

  return (
    <section
      className="settings-group help-menu-sections"
      aria-labelledby="help-menu-sections-title"
    >
      <div className="panel-head">
        <h2 id="help-menu-sections-title">
          {i18n.t("settings:helpMenu.title")}
        </h2>
        <button
          type="button"
          disabled={!value.length}
          onClick={() => onChange([])}
        >
          {i18n.t("settings:helpMenu.reset")}
        </button>
      </div>
      <p className="muted">{i18n.t("settings:helpMenu.hint")}</p>
      {catalog.isPending && (
        <p role="status">{i18n.t("settings:helpMenu.loading")}</p>
      )}
      {catalog.isError && (
        <p role="alert">
          {i18n.t("settings:helpMenu.loadFailed", {
            error: catalog.error.message,
          })}
        </p>
      )}
      {rows.map(({ id, title, visible, index }, position) => (
        <div className="list-row" key={id}>
          <label className="field field-toggle">
            <span className="field-name">{title}</span>
            <input
              type="checkbox"
              aria-label={i18n.t("settings:helpMenu.show", { title })}
              checked={visible}
              onChange={(event) => {
                const visible = event.target.checked;
                onChange(
                  order.map((section) =>
                    section.id === id ? { id, visible } : section,
                  ),
                );
              }}
            />
          </label>
          <button
            type="button"
            className="icon-button"
            aria-label={i18n.t("settings:helpMenu.moveUp", { title })}
            disabled={position === 0}
            onClick={() => move(index, rows[position - 1].index)}
          >
            <ArrowUp size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label={i18n.t("settings:helpMenu.moveDown", { title })}
            disabled={position === rows.length - 1}
            onClick={() => move(index, rows[position + 1].index)}
          >
            <ArrowDown size={16} aria-hidden="true" />
          </button>
        </div>
      ))}
    </section>
  );
}
