import {
  CircleCheck,
  CircleX,
  Clock3,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { toolFailure, type ToolItem } from "../lib/timeline";
import { ShellCall } from "./ShellCall";
import i18n from "../i18n";

function pretty(text = "") {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

function argumentPreview(text: string) {
  try {
    const args = JSON.parse(text);
    if (args && typeof args === "object" && !Array.isArray(args)) {
      return Object.entries(args)
        .map(
          ([key, value]) =>
            `${key}: ${typeof value === "string" ? value : JSON.stringify(value)}`,
        )
        .join(" · ");
    }
  } catch {
    /* Plain text arguments are also supported. */
  }
  return text;
}

export function ToolGroup({ items }: { items: ToolItem[] }) {
  return (
    <div className="tool-trace" aria-label={i18n.t("chat:tools.calls")}>
      {items.map(({ call, result }) => {
        if (call.function.name === "shell")
          return <ShellCall key={call.id} item={{ call, result }} />;
        const failed = toolFailure(result);
        const StatusIcon = !result ? Clock3 : failed ? CircleX : CircleCheck;
        return (
          <div className="trace-step" key={call.id}>
            <details>
              <summary>
                <span className="trace-name">
                  <Wrench size={14} />
                  {call.function.name}
                  <ChevronRight size={12} />
                </span>
                <span className="trace-preview">
                  {argumentPreview(call.function.arguments) ||
                    i18n.t("chat:tools.noArgs")}
                </span>
              </summary>
              <div className="trace-details">
                <strong>{i18n.t("chat:tools.args")}</strong>
                <pre>{pretty(call.function.arguments)}</pre>
                {result && (
                  <>
                    <strong>{i18n.t("chat:tools.result")}</strong>
                    <pre>{pretty(result.content)}</pre>
                  </>
                )}
              </div>
            </details>
            <div
              className={`trace-status ${!result ? "waiting" : failed ? "failed" : "done"}`}
            >
              <StatusIcon size={13} />
              {!result
                ? i18n.t("chat:tools.running")
                : failed
                  ? i18n.t("chat:tools.failed")
                  : i18n.t("chat:tools.done")}
            </div>
          </div>
        );
      })}
    </div>
  );
}
