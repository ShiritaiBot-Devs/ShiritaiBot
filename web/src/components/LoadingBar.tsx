/** Indeterminate progress, with a screen-reader label instead of visible text. */
export function LoadingBar({
  label = "正在加载",
  compact = false,
}: {
  label?: string;
  compact?: boolean;
}) {
  return (
    <span
      className={"loading-bar" + (compact ? " loading-bar--compact" : "")}
      role="progressbar"
      aria-label={label}
      aria-busy="true"
    />
  );
}
