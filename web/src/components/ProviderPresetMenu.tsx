import { useEffect, useRef, useState } from "react";
import { Plus } from "lucide-react";
import {
  PROVIDER_PRESETS,
  type ProviderPreset,
} from "../config/provider-presets";

export function ProviderPresetMenu({
  onSelect,
}: {
  onSelect: (preset: ProviderPreset) => void;
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) menu.current?.querySelector("button")?.focus();
  }, [open]);

  function show() {
    const bounds = trigger.current?.getBoundingClientRect();
    if (!bounds || !menu.current) return;
    menu.current.style.top = `${bounds.bottom + 6}px`;
    menu.current.style.left = `${Math.max(8, Math.min(bounds.right - 260, window.innerWidth - 268))}px`;
    menu.current.showPopover();
  }

  return (
    <div
      className="provider-preset-picker"
      ref={box}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget))
          menu.current?.hidePopover();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          menu.current?.hidePopover();
          trigger.current?.focus();
        }
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key))
          return;
        event.preventDefault();
        if (!open) {
          show();
          return;
        }
        const options = Array.from(
          menu.current?.querySelectorAll("button") ?? [],
        );
        const index = options.indexOf(
          document.activeElement as HTMLButtonElement,
        );
        const next =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? options.length - 1
              : (index +
                  (event.key === "ArrowDown" ? 1 : -1) +
                  options.length) %
                options.length;
        options[next]?.focus();
      }}
    >
      <button
        type="button"
        ref={trigger}
        aria-label="新增提供商"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => (open ? menu.current?.hidePopover() : show())}
      >
        <Plus size={16} />
      </button>
      <div
        ref={menu}
        popover="auto"
        onToggle={(event) => setOpen(event.newState === "open")}
        className="provider-preset-menu"
        role="menu"
        aria-label="提供商预设"
      >
        {PROVIDER_PRESETS.map((preset) => (
          <button
            type="button"
            key={preset.id}
            role="menuitem"
            onClick={() => {
              menu.current?.hidePopover();
              trigger.current?.focus();
              onSelect(preset);
            }}
          >
            <strong>{preset.label}</strong>
            <small>{preset.protocol}</small>
          </button>
        ))}
      </div>
    </div>
  );
}
