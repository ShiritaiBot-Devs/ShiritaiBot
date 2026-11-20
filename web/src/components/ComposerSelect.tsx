import { useEffect, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import type { ComposerOption } from "../lib/composer";

export function ComposerSelect<T extends string>({
  label,
  value,
  options,
  groups = [],
  onSelect,
  display,
}: {
  label: string;
  value: T | "";
  options: ComposerOption<T>[];
  groups?: { label: string; options: ComposerOption<T>[] }[];
  onSelect: (value: T) => void;
  display?: string;
}) {
  const [open, setOpen] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    function close(event: MouseEvent) {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  function renderOption(option: ComposerOption<T>) {
    return (
      <li key={option.value} role="presentation">
        <button
          type="button"
          role="option"
          aria-selected={option.value === value}
          onClick={() => {
            setOpen(false);
            triggerRef.current?.focus();
            onSelect(option.value);
          }}
        >
          <strong>{option.label}</strong>
          <small>{option.hint}</small>
        </button>
      </li>
    );
  }

  return (
    <div
      className="composer-select"
      ref={boxRef}
      onKeyDown={(event) => {
        if (open && event.key === "Escape") {
          setOpen(false);
          triggerRef.current?.focus();
        }
      }}
    >
      <button
        type="button"
        ref={triggerRef}
        className="composer-chip"
        title={display || label}
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span>{display || label}</span>
        <ChevronDown size={13} aria-hidden="true" />
      </button>
      {open && (
        <ul className="composer-popover" role="listbox" aria-label={label}>
          {options.map(renderOption)}
          {groups.map((group) => (
            <li key={group.label} role="presentation">
              <div className="composer-group-label" aria-hidden="true">
                {group.label}
              </div>
              <ul role="group" aria-label={group.label}>
                {group.options.map(renderOption)}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
