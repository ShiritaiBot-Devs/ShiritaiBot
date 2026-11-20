export function PageTabs<T extends string>({
  tabs,
  active,
  onSelect,
  prefix,
  label,
}: {
  tabs: readonly { id: T; label: string }[];
  active: T;
  onSelect: (id: T) => void;
  prefix: string;
  label: string;
}) {
  return (
    <div className="settings-tabs" role="tablist" aria-label={label}>
      {tabs.map(({ id, label }, index) => (
        <button
          key={id}
          type="button"
          role="tab"
          id={`${prefix}-tab-${id}`}
          aria-controls={`${prefix}-panel-${id}`}
          aria-selected={active === id}
          tabIndex={active === id ? 0 : -1}
          onClick={() => onSelect(id)}
          onKeyDown={(event) => {
            let next = index;
            if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
            else if (event.key === "ArrowLeft")
              next = (index + tabs.length - 1) % tabs.length;
            else if (event.key === "Home") next = 0;
            else if (event.key === "End") next = tabs.length - 1;
            else return;
            event.preventDefault();
            onSelect(tabs[next].id);
            document.getElementById(`${prefix}-tab-${tabs[next].id}`)?.focus();
          }}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
