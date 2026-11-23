/** Indeterminate progress, with a screen-reader label instead of visible text. */
import i18n from "../i18n";

export function LoadingBar({
  label = i18n.t("loading"),
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
