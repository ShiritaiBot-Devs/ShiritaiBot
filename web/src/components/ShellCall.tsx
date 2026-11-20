import { useState } from "react";
import {
  Check,
  ChevronRight,
  Copy,
  FilePenLine,
  SquareTerminal,
  X,
  Clock3,
} from "lucide-react";
import type { FileDiff } from "../api/types";
import type { ToolItem } from "../lib/timeline";
import { useNotice } from "../hooks/useNotice";

function json(text?: string) {
  try {
    return JSON.parse(text || "") as Record<string, unknown> | null;
  } catch {
    return null;
  }
}

function CopyText({ text, label }: { text: string; label: string }) {
  const [copied, setCopied] = useState(false);
  const { fail } = useNotice();
  async function copy() {
    try {
      if (navigator.clipboard) await navigator.clipboard.writeText(text);
      else {
        const el = document.createElement("textarea");
        el.value = text;
        document.body.append(el);
        el.select();
        try {
          if (!document.execCommand("copy")) throw new Error();
        } finally {
          el.remove();
        }
      }
      setCopied(true);
    } catch {
      fail("复制失败，可选中文本后复制。");
    }
  }
  return (
    <button
      type="button"
      className="trace-copy"
      aria-label={label}
      title={copied ? "已复制" : label}
      onBlur={() => setCopied(false)}
      onClick={() => void copy()}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
    </button>
  );
}

function stdoutDiff(text: string): FileDiff[] {
  const files: FileDiff[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let file: FileDiff | undefined;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (
      line.startsWith("--- ") &&
      lines[i + 1]?.startsWith("+++ ") &&
      lines[i + 2]?.startsWith("@@ ")
    ) {
      const next = lines[++i].slice(4).split("\t")[0];
      file = {
        path: (next === "/dev/null"
          ? line.slice(4).split("\t")[0]
          : next
        ).replace(/^[ab]\//, ""),
        patch: "",
        added: 0,
        removed: 0,
      };
      files.push(file);
      continue;
    }
    if (!file) continue;
    if (!line || !/^(?:@@ |[ +\\-])/.test(line)) {
      file = undefined;
      continue;
    }
    file.patch += (file.patch ? "\n" : "") + line;
    if (line.startsWith("+")) file.added++;
    if (line.startsWith("-")) file.removed++;
  }
  return files;
}

function FilePatch({ file }: { file: FileDiff }) {
  let left = 0,
    right = 0;
  return (
    <section className="patch-file" aria-label={file.path}>
      <div className="terminal-caption">
        <strong title={file.path}>{file.path}</strong>
        <span className="patch-add-count">+{file.added}</span>
        <span className="patch-remove-count">−{file.removed}</span>
        {file.patch && (
          <CopyText text={file.patch} label={`复制 ${file.path} 的差异`} />
        )}
      </div>
      {file.patch && (
        <div className="patch-scroll" tabIndex={0}>
          <div className="patch-lines">
            {file.patch.split("\n").map((line, i) => {
              const header = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(
                line,
              );
              if (header) {
                left = Number(header[1]);
                right = Number(header[2]);
                return (
                  <div key={i} className="patch-hunk">
                    {line}
                  </div>
                );
              }
              const sign = line[0];
              const old = sign === " " || sign === "-" ? left++ : "";
              const next = sign === " " || sign === "+" ? right++ : "";
              return (
                <div
                  className={`patch-line ${sign === "+" ? "added" : sign === "-" ? "removed" : ""}`}
                  key={i}
                >
                  <span className="line-number">{old}</span>
                  <span className="line-number">{next}</span>
                  <code>{line}</code>
                </div>
              );
            })}
          </div>
        </div>
      )}
      {file.truncated && <small>差异预览已截断</small>}
    </section>
  );
}

export function ShellCall({ item }: { item: ToolItem }) {
  const { call, result } = item;
  const args = json(call.function.arguments);
  const command =
    typeof args?.command === "string" ? args.command : call.function.arguments;
  const parsed = json(result?.content);
  const output = result?._shell_output ?? parsed;
  const stdout = typeof output?.stdout === "string" ? output.stdout : "";
  const stderr = typeof output?.stderr === "string" ? output.stderr : "";
  const exit =
    typeof output?.exit_code === "number" ? output.exit_code : undefined;
  const error = typeof parsed?.error === "string" ? parsed.error : "";
  const failed = Boolean(error) || (exit !== undefined && exit !== 0);
  const label = !result
    ? "正在运行"
    : failed
      ? "运行失败"
      : exit === 0
        ? "已运行"
        : "运行记录";
  const fallback =
    typeof parsed?.preview === "string" ? parsed.preview : result?.content;
  const transcript = [
    "$ " + command,
    stdout,
    stderr,
    error,
    exit === undefined && !error ? fallback : "",
  ]
    .filter(Boolean)
    .join("\n");
  const changes = result?._file_diffs;
  const files = changes?.length ? changes : stdoutDiff(stdout);
  return (
    <div className="shell-step">
      <details className="shell-call">
        <summary title={command}>
          <SquareTerminal size={14} />
          <span>
            {label} <code>{command}</code>
          </span>
          <ChevronRight size={13} />
        </summary>
        <div className="terminal-card">
          <div className="terminal-caption">
            Shell
            <CopyText text={transcript} label="复制命令和输出" />
          </div>
          <pre tabIndex={0}>{transcript}</pre>
          <div className={`terminal-status ${failed ? "failed" : ""}`}>
            {output?.truncated === true && <span>输出已截断</span>}
            {!result ? (
              <>
                <Clock3 size={12} />
                执行中
              </>
            ) : failed ? (
              <>
                <X size={13} />
                {exit !== undefined ? `退出码 ${exit}` : "失败"}
              </>
            ) : exit === 0 ? (
              <>
                <Check size={13} />
                成功
              </>
            ) : (
              "已返回"
            )}
          </div>
        </div>
      </details>
      {files.length > 0 && (
        <details className="shell-files" open>
          <summary>
            <FilePenLine size={14} />
            {changes?.length ? "已编辑的文件" : "差异输出"}
            <ChevronRight size={13} />
          </summary>
          {files.map((file, i) => (
            <FilePatch key={i} file={file} />
          ))}
        </details>
      )}
    </div>
  );
}
