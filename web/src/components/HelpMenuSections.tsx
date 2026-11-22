import { useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { HelpMenuSectionSetting } from "../api/types";

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
        <h2 id="help-menu-sections-title">/help 菜单分区</h2>
        <button
          type="button"
          disabled={!value.length}
          onClick={() => onChange([])}
        >
          恢复默认
        </button>
      </div>
      <p className="muted">文字和图片菜单共用分区设置。用右侧箭头调整顺序。</p>
      {catalog.isPending && <p role="status">正在加载分区…</p>}
      {catalog.isError && (
        <p role="alert">读取分区失败：{catalog.error.message}</p>
      )}
      {rows.map(({ id, title, visible, index }, position) => (
        <div className="list-row" key={id}>
          <label className="field field-toggle">
            <span className="field-name">{title}</span>
            <input
              type="checkbox"
              aria-label={`显示 ${title}`}
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
            aria-label={`上移 ${title}`}
            disabled={position === 0}
            onClick={() => move(index, rows[position - 1].index)}
          >
            <ArrowUp size={16} aria-hidden="true" />
          </button>
          <button
            type="button"
            className="icon-button"
            aria-label={`下移 ${title}`}
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
