import {
  CircleCheck,
  CircleX,
  Clock3,
  ChevronRight,
  Wrench,
} from "lucide-react";
import { toolFailure, type ToolItem } from "../lib/timeline";
import { ShellCall } from "./ShellCall";

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
    <div className="tool-trace" aria-label="工具调用">
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
                  {argumentPreview(call.function.arguments) || "无参数"}
                </span>
              </summary>
              <div className="trace-details">
                <strong>参数</strong>
                <pre>{pretty(call.function.arguments)}</pre>
                {result && (
                  <>
                    <strong>返回结果</strong>
                    <pre>{pretty(result.content)}</pre>
                  </>
                )}
              </div>
            </details>
            <div
              className={`trace-status ${!result ? "waiting" : failed ? "failed" : "done"}`}
            >
              <StatusIcon size={13} />
              {!result ? "执行中" : failed ? "失败" : "已完成"}
            </div>
          </div>
        );
      })}
    </div>
  );
}
