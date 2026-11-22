import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Send, Square, X } from "lucide-react";
import type { ApiClient } from "../api/client";
import type { Action } from "../pages/common";
import { ComposerSelect } from "./ComposerSelect";
import { configuredModelName, groupModels, modelNameById } from "../lib/models";
import {
  composerHeight,
  draftKey,
  modelKey,
  PRESETS,
  readSendMode,
  readValue,
  SEND_MODES,
  stepIndex,
  writeSendMode,
  writeValue,
  type Preset,
  type SendMode,
} from "../lib/composer";
import { matchPreset } from "../lib/permissions";

export interface ChatComposerProps {
  client: ApiClient;
  act: Action;
  session: string;
  assistantName: string;
  running?: boolean;
  onSend: (text: string) => void;
}

interface Queued {
  id: number;
  text: string;
}

export function ChatComposer({
  client,
  act,
  session,
  assistantName,
  running = false,
  onSend,
}: ChatComposerProps) {
  const [text, setText] = useState(() => readValue(draftKey(session)));
  const [lastSent, setLastSent] = useState("");
  const [queue, setQueue] = useState<Queued[]>([]);
  const [sendMode, setSendMode] = useState<SendMode>(readSendMode);
  const [model, setModel] = useState(() => readValue(modelKey(session)));
  const [preset, setPreset] = useState<Preset | "">("");
  const [dismissed, setDismissed] = useState(false);
  const [index, setIndex] = useState(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wasRunning = useRef(running);
  const nextId = useRef(0);

  const config = useQuery({
    queryKey: ["config"],
    queryFn: () => client.config(),
  });
  const tools = useQuery({
    queryKey: ["tools", session],
    queryFn: () => client.tools(session),
  });
  const commands = useQuery({
    queryKey: ["commands", session],
    queryFn: () => client.commands(session),
  });

  useEffect(() => {
    writeValue(draftKey(session), text);
  }, [session, text]);

  useEffect(() => {
    const element = inputRef.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = composerHeight(element.scrollHeight) + "px";
  }, [text]);

  useEffect(() => {
    const settled = wasRunning.current && !running;
    wasRunning.current = running;
    if (!settled || queue.length === 0) return;
    const [head] = queue;
    setQueue((items) => items.slice(1));
    onSend(head.text);
  }, [running, queue, onSend]);

  const providers = new Set(
    (config.data?.providers ?? [])
      .filter((row) => row.enabled)
      .map((row) => row.id),
  );
  const models = (config.data?.models ?? []).filter(
    (row) => row.enabled && row.kind === "text" && providers.has(row.provider),
  );
  const modelGroups = [...groupModels(models)].map(([provider, group]) => ({
    label: provider,
    options: group.map((row) => ({
      value: row.id,
      label: configuredModelName(row),
      hint: row.id,
    })),
  }));
  const modelLabel = model
    ? modelNameById(config.data?.models ?? [], model)
    : "模型";
  const serverPreset = tools.data?.policy?.risks
    ? matchPreset(tools.data.policy.risks)
    : "balanced";
  const currentPreset = preset || serverPreset;
  const presetLabel =
    currentPreset === "custom"
      ? "自定义"
      : (PRESETS.find((row) => row.value === currentPreset)?.label ?? "审查");
  const sendLabel =
    SEND_MODES.find((row) => row.value === sendMode)?.label ?? "发送性质";

  const query = text.trimStart();
  const menuOpen =
    query.startsWith("/") &&
    !/\s$/.test(query) &&
    !dismissed &&
    Boolean(commands.data);
  const matches = menuOpen
    ? (commands.data ?? []).filter((command) =>
        command.name.startsWith(query.slice(1).toLowerCase()),
      )
    : [];
  const active = matches.length ? Math.min(index, matches.length - 1) : -1;
  useEffect(() => {
    if (active >= 0)
      document
        .getElementById(`command-option-${active}`)
        ?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function pick(name: string) {
    setText("/" + name + " ");
    setDismissed(true);
    setIndex(0);
    inputRef.current?.focus();
  }

  function stop() {
    onSend("/stop");
  }

  function send() {
    const message = text.trim();
    if (!message) return;
    setLastSent(message);
    setText("");
    writeValue(draftKey(session), "");
    setDismissed(false);
    if (running && sendMode === "queue") {
      nextId.current += 1;
      setQueue((items) => [...items, { id: nextId.current, text: message }]);
      return;
    }
    onSend(message);
  }

  function chooseModel(id: string) {
    setModel(id);
    writeValue(modelKey(session), id);
    void act(
      () => client.sendMessage(id ? `/model ${id}` : "/model default", session),
      "",
    );
  }

  function choosePreset(value: Preset) {
    setPreset(value);
    void act(() => client.sendMessage(`/tools preset ${value}`, session), "");
  }

  function chooseSendMode(value: SendMode) {
    setSendMode(value);
    writeSendMode(value);
  }

  function onKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    const native = event.nativeEvent as KeyboardEvent & {
      isComposing?: boolean;
    };
    if (native.isComposing || native.keyCode === 229) return;
    if (matches.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setIndex(stepIndex(active, 1, matches.length));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setIndex(stepIndex(active, -1, matches.length));
        return;
      }
      if (
        event.key === "Tab" ||
        (event.key === "Enter" &&
          !event.shiftKey &&
          query !== "/" + matches[active].name)
      ) {
        event.preventDefault();
        pick(matches[active].name);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setDismissed(true);
        return;
      }
    }
    if (event.key === "Escape") {
      if (running) {
        event.preventDefault();
        stop();
      }
      return;
    }
    if (event.key === "ArrowUp" && text === "" && lastSent) {
      event.preventDefault();
      setText(lastSent);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <form
      className="composer"
      onSubmit={(event) => {
        event.preventDefault();
        send();
      }}
    >
      {queue.length > 0 && (
        <ul className="composer-queue" aria-label="排队消息">
          {queue.map((item) => (
            <li key={item.id}>
              <span>{item.text}</span>
              <button
                type="button"
                className="icon-button"
                aria-label={`移除排队消息：${item.text}`}
                onClick={() =>
                  setQueue((items) => items.filter((row) => row.id !== item.id))
                }
              >
                <X size={13} aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      )}
      {matches.length > 0 && (
        <div
          className="command-menu"
          role="listbox"
          id="chat-commands"
          aria-label="斜杠命令"
        >
          {matches.map((command, position) => (
            <button
              type="button"
              role="option"
              key={command.name}
              id={`command-option-${position}`}
              tabIndex={-1}
              onMouseDown={(event) => event.preventDefault()}
              aria-selected={position === active}
              onClick={() => pick(command.name)}
            >
              <strong>/{command.name}</strong>
              <span>{command.description}</span>
            </button>
          ))}
        </div>
      )}
      <textarea
        aria-label="消息"
        aria-autocomplete="list"
        aria-controls={matches.length ? "chat-commands" : undefined}
        aria-activedescendant={
          active >= 0 ? `command-option-${active}` : undefined
        }
        ref={inputRef}
        value={text}
        rows={1}
        onChange={(event) => {
          setText(event.target.value);
          setDismissed(false);
          setIndex(0);
        }}
        onKeyDown={onKeyDown}
        placeholder={`告诉 ${assistantName} 你想做什么，或输入 / 查看命令…`}
      />
      <div className="composer-bar">
        <div className="composer-chips">
          <ComposerSelect
            label="模型"
            value={model}
            display={modelLabel}
            options={[
              {
                value: "",
                label: "默认路由",
                hint: "按配置的模型路由自动选择",
              },
            ]}
            groups={modelGroups}
            onSelect={chooseModel}
          />
          <ComposerSelect
            label="发送性质"
            value={sendMode}
            display={sendLabel}
            options={SEND_MODES}
            onSelect={chooseSendMode}
          />
          <ComposerSelect
            label="审查方式"
            value={currentPreset === "custom" ? ("" as Preset) : currentPreset}
            display={presetLabel}
            options={PRESETS}
            onSelect={choosePreset}
          />
        </div>
        <div className="composer-actions">
          {running && (
            <button type="button" aria-label="停止当前任务" onClick={stop}>
              <Square size={16} />
            </button>
          )}
          <button
            type="submit"
            className="primary"
            disabled={!text.trim()}
            aria-label="发送消息"
          >
            <Send size={17} />
          </button>
        </div>
      </div>
    </form>
  );
}
