from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from shiritaibot.i18n import Text, join_list, t
from shiritaibot.prompts import prompt as prompt_text

if TYPE_CHECKING:
    from shiritaibot.agent.goals import Goals
    from shiritaibot.subagent.native import NativeRunner

from shiritaibot.core import NEEDS_INPUT, Context, ExternalEvent, PublicError, uid
from shiritaibot.core.logs import brief, get_logger
from shiritaibot.core.process import run_process

log = get_logger("subagent.log.source")


# Stdout retained by run_process. NDJSON kinds lose nothing important past this, since their
# events are parsed as they stream. claude_code prints one JSON object at the very end, so no
# prefix or suffix of it is parseable: it gets a limit far above any realistic reply instead.
OUTPUT_LIMIT = 256000
OUTPUT_LIMITS = {"claude_code": 16 * 1024 * 1024}
STREAMED = {"codex", "deepseek_harness", "antigravity"}
RESULT_LIMIT = 16000
TEXT_LIMIT = 256000


def command_for(config, workspace: Path, resume: str | None = None) -> list[str]:
    args = list(config.command)
    if config.kind == "codex":
        # `exec resume` rejects --sandbox/-C/--add-dir, so permissions go through -c overrides
        # (same keys as ~/.codex/config.toml) for both fresh and resumed runs.
        args += ["exec", "resume"] if resume else ["exec"]
        args += [
            "--json",
            "--skip-git-repo-check",
            "-c",
            f"sandbox_mode={json.dumps(config.sandbox)}",
        ]
        if config.sandbox == "workspace-write":
            args += [
                "-c",
                f"sandbox_workspace_write.network_access={str(config.network_access).lower()}",
            ]
            if config.add_dirs:
                args += [
                    "-c",
                    f"sandbox_workspace_write.writable_roots={json.dumps(config.add_dirs)}",
                ]
        if config.model:
            args += ["--model", config.model]
        if resume:
            args += [resume]
        else:
            args += ["-C", str(workspace)]
        args += ["-"]
    elif config.kind == "claude_code":
        args += [
            "-p",
            "--output-format",
            "json",
            "--permission-mode",
            config.permission_mode,
        ]
        if config.tools:
            args += ["--tools", ",".join(config.tools)]
        if config.allowed_tools:
            args += ["--allowedTools", ",".join(config.allowed_tools)]
        if config.disallowed_tools:
            args += ["--disallowedTools", ",".join(config.disallowed_tools)]
        for directory in config.add_dirs:
            args += ["--add-dir", directory]
        if config.model:
            args += ["--model", config.model]
        if resume:
            args += ["--resume", resume]
    elif config.kind == "antigravity":
        args += ["--input-format", "stream-json", "--output-format", "stream-json"]
        if config.model:
            args += ["--model", config.model]
        if config.antigravity_mode:
            args += ["--mode", config.antigravity_mode]
        for directory in config.add_dirs:
            args += ["--add-dir", directory]
        if resume:
            args += ["--conversation", resume]
    elif config.kind == "deepseek_harness":
        # `--profile headless` is a launcher flag and must precede the app arguments. Policy
        # options apply per invocation (they are not saved), so resumes repeat them; resuming
        # also requires the same cwd, which the run's workspace guarantees. No model flag
        # exists: the route comes from the dsh profile.
        args += [
            "--profile",
            "headless",
            "--json",
            "--permission-mode",
            config.permission_preset,
            "--approval-policy",
            config.approval_policy,
        ]
        for directory in config.add_dirs:
            args += ["--add-dir", directory]
        if resume:
            args += ["--session-id", resume]
        args += ["-"]
    return args


def permissions(config) -> dict:
    """The effective permission settings of an instance, as the agent should see them."""
    if config.kind == "shiritaibot":
        return {
            "tools": config.native_tools,
            "skills": config.native_skills,
            "manual_approval": config.native_manual_approval,
        }
    if config.kind == "codex":
        return {
            "sandbox": config.sandbox,
            "network_access": config.sandbox == "danger-full-access"
            or (config.sandbox == "workspace-write" and config.network_access),
            "add_dirs": config.add_dirs,
        }
    if config.kind == "claude_code":
        return {
            "permission_mode": config.permission_mode,
            "tools": config.tools or "default",
            "allowed_tools": config.allowed_tools,
            "disallowed_tools": config.disallowed_tools,
            "add_dirs": config.add_dirs,
        }
    if config.kind == "antigravity":
        return {"mode": config.antigravity_mode or "CLI default", "add_dirs": config.add_dirs}
    if config.kind == "deepseek_harness":
        return {
            "permission_preset": config.permission_preset,
            "approval_policy": config.approval_policy,
            "add_dirs": config.add_dirs,
        }
    return {}


# dsh reports a rejected approval or sandbox block as a tool_result error code.
DSH_DENIAL = ("APPROVAL", "DENIED", "REJECT", "PERMISSION", "SANDBOX")


def denial(tool: str, detail) -> str:
    target = next(
        (
            detail[k]
            for k in ("command", "file_path", "path", "url", "pattern")
            if isinstance(detail, dict) and k in detail
        ),
        "",
    )
    return f"{tool}({brief(str(target), 120)})" if target else tool


class OutputParser:
    """Extract result text, native session ID, error flag and permission denials.

    NDJSON kinds (STREAMED) take every line through feed() while the process runs, because
    their final events sit at the tail of the stream, past what run_process retains. The rest
    are parsed from the retained stdout in finish().
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.out = {"result": "", "native": None, "error": False, "denied": []}
        # Whether finish() found real result text rather than falling back to raw stdout.
        self.complete = kind not in {*STREAMED, "claude_code"}
        self.texts: deque[str] = deque()
        self.text_size = 0
        self.calls: dict = {}
        self.final: dict | None = None
        self.failure = ""

    def add_text(self, text) -> None:
        if not isinstance(text, str):
            return
        self.texts.append(text)
        self.text_size += len(text)
        # Long runs narrate a lot; only the latest text can reach the stored result.
        while self.text_size > TEXT_LIMIT and len(self.texts) > 1:
            self.text_size -= len(self.texts.popleft())

    def feed(self, line: bytes | str) -> None:
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="replace")
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        if self.kind == "codex":
            self.feed_codex(obj)
        elif self.kind == "deepseek_harness":
            self.feed_deepseek(obj)
        elif self.kind == "antigravity":
            if obj.get("event") == "init":
                self.out["native"] = obj.get("conversation_id")
            elif obj.get("event") == "result" and isinstance(final := obj.get("result"), dict):
                self.final = final
                self.out["native"] = final.get("conversation_id") or self.out["native"]

    def feed_codex(self, obj: dict) -> None:
        out = self.out
        out["native"] = obj.get("thread_id", out["native"])
        item = obj.get("item")
        if isinstance(item, dict) and item.get("type") == "agent_message":
            self.add_text(item.get("text", ""))
        if obj.get("type") in {"turn.failed", "error"}:
            out["error"] = True
            error = obj.get("error")
            message = error.get("message") if isinstance(error, dict) else obj.get("message")
            self.failure = str(message or self.failure)

    def feed_deepseek(self, obj: dict) -> None:
        # NDJSON from `dsh headless --json`: session ... final (or error without final).
        out = self.out
        event = obj.get("type")
        if event == "session":
            out["native"] = obj.get("sessionId") or out["native"]
        elif event == "text":
            self.add_text(obj.get("text", ""))
        elif event == "tool_call":
            self.calls[obj.get("callId")] = denial(obj.get("tool", "?"), obj.get("input"))
        elif event == "tool_result":
            call = self.calls.pop(obj.get("callId"), "?")
            if isinstance(obj.get("error"), dict):
                code = str(obj["error"].get("code", "")).upper()
                if any(word in code for word in DSH_DENIAL):
                    out["denied"].append(call)
        elif event == "error":
            out["error"] = True
            self.add_text(obj.get("message", ""))
        elif event == "final":
            self.final = obj
            out["native"] = obj.get("sessionId") or out["native"]

    def finish(self, stdout: str) -> dict:
        out = self.out
        out["result"] = stdout
        if self.kind == "claude_code":
            self.finish_claude(stdout)
        elif self.kind == "antigravity":
            self.finish_antigravity()
        elif self.kind == "codex":
            self.finish_text("\n".join(self.texts) or self.failure, stdout)
        elif self.kind == "deepseek_harness":
            final = self.final or {}
            reason = final.get("reason")
            kind = reason.get("kind") if isinstance(reason, dict) else None
            out["error"] = out["error"] or kind != "completed"
            texts = "\n".join(t for t in self.texts if t)
            self.finish_text(final.get("text") or texts, stdout)
        return out

    def finish_antigravity(self) -> None:
        final = self.final or {}
        status = final.get("status")
        response = final.get("response")
        response = response.strip() if isinstance(response, str) else ""
        reason = final.get("finished_reason")
        if status == "WAITING" and self.out["native"]:
            self.out["question"] = question_in(response) or response or t("subagent.needsInputDefault")
        else:
            self.out["error"] = (
                status != "SUCCESS"
                or not response
                or reason in ("print_timeout", "interrupted", "error")
            )
        detail = (final.get("error") or reason) if self.out["error"] else None
        if self.out["error"] and not detail:
            detail = (
                t("subagent.antigravity.status", status=status)
                if status
                else t("subagent.antigravity.noResult")
            )
        self.out["result"] = "\n".join(str(part) for part in (response, detail) if part)
        self.complete = self.final is not None

    def finish_text(self, text: str, stdout: str) -> None:
        self.out["result"] = text or stdout
        self.complete = bool(text)

    def finish_claude(self, stdout: str) -> None:
        out = self.out
        try:
            obj = json.loads(stdout)
        except ValueError:
            return
        if isinstance(obj, dict):
            self.complete = True
            out.update(
                result=obj.get("result") or stdout,
                native=obj.get("session_id"),
                error=bool(obj.get("is_error")),
            )
            for denied in obj.get("permission_denials") or []:
                if isinstance(denied, dict):
                    tool = denied.get("tool_name", "?")
                    out["denied"].append(denial(tool, denied.get("tool_input") or {}))


def parse_output(kind: str, stdout: str) -> dict:
    """Parse a complete stdout in one go."""
    parser = OutputParser(kind)
    if kind in STREAMED:
        # Not splitlines(): JSON strings may hold raw line separators such as U+2028.
        for line in stdout.split("\n"):
            parser.feed(line)
    return parser.finish(stdout)


def clip(text: str, limit: int = RESULT_LIMIT) -> str:
    """Shorten text to limit, keeping both ends: the final message is at the tail."""
    if len(text) <= limit:
        return text
    head = limit // 4
    marker = prompt_text("SUBAGENT_OMITTED")
    return text[:head] + marker + text[-(limit - head - len(marker)) :]


def question_in(text: str) -> str | None:
    """The NEEDS_INPUT question at the end of a reply, if the agent asked one."""
    for line in reversed(text.strip().splitlines()[-5:]):
        line = line.strip().strip("*`").strip()
        if line.upper().startswith(NEEDS_INPUT):
            return line[len(NEEDS_INPUT) :].strip() or t("subagent.needsInputDefault")
    return None


class SubagentManager:
    def __init__(self, config, store, home: Path, emit, native: NativeRunner | None = None):
        self.native = native
        self.resume_lock = asyncio.Lock()
        self.config, self.store, self.home, self.emit = config, store, home, emit
        self.tasks: dict[str, asyncio.Task] = {}
        self.goals: Goals | None = None
        self.semaphore = asyncio.Semaphore(3)
        # Environment for child CLIs (plugin-provided git identity); None inherits the host.
        self.env: Callable[[], dict[str, str] | None] = lambda: None
        # Plugin instructions for fresh runs (e.g. commit trailers); empty string for none.
        self.notes: Callable[[], str] = lambda: ""

    def instances(self) -> list[dict]:
        """Describe enabled instances from live config without exposing CLI arguments."""
        return [
            {
                "id": instance.id,
                "kind": instance.kind,
                "model": instance.model,
                "permissions": permissions(instance),
                **(
                    {"instructions": instance.instructions}
                    if instance.kind == "shiritaibot"
                    else {}
                ),
            }
            for instance in self.config.value.subagents
            if instance.enabled
        ]

    def child_env(self, config) -> dict[str, str] | None:
        base = self.env()
        if not config.proxy:
            return base
        env = dict(os.environ if base is None else base)
        for key in ("http_proxy", "https_proxy", "all_proxy"):
            env[key] = env[key.upper()] = config.proxy
        return env

    def instance(self, name: str):
        config = next((x for x in self.config.value.subagents if x.id == name and x.enabled), None)
        if not config:
            raise PublicError("subagent.missing")
        return config.model_copy(deep=True)

    def launch(self, row, ctx: Context, config, workspace: Path, prompt: str, resume=None):
        key = row["id"]
        task = asyncio.create_task(self.run(dict(row), ctx, config, workspace, prompt, resume))
        self.tasks[key] = task
        def cleanup(finished):
            if self.tasks.get(key) is finished:
                self.tasks.pop(key, None)
        task.add_done_callback(cleanup)

    def _validate_dynamic_model(self, model: str) -> str:
        model = model.strip()
        if not model:
            return ""
        if not self.config.value.policy.dynamic_subagent_model_override:
            raise PublicError("subagent.noModelOverride")
        available = self.config.value.text_generation_models()
        if model not in available:
            hint = t("subagent.availableModels", models=', '.join(available)) if available else ""
            raise PublicError("subagent.modelInvalid", model=model, hint=hint)
        return model

    def _dynamic_spec(self, row: dict):
        from shiritaibot.subagent.native import DynamicSubagentSpec

        if not self.config.value.policy.dynamic_subagent:
            raise PublicError("subagent.dynamicDisabled")
        raw = row.get("dynamic_spec")
        if not isinstance(raw, dict):
            raise PublicError("subagent.dynamicConfigMissing")
        model = self._validate_dynamic_model(str(raw.get("model", "")))
        tools = raw.get("tools")
        skills = raw.get("skills")
        return DynamicSubagentSpec(
            id=row["instance"],
            instructions=str(raw.get("instructions", "")),
            model=model,
            tools=tuple(tools) if isinstance(tools, list) else None,
            skills=tuple(skills) if isinstance(skills, list) else None,
            read_only=bool(raw.get("read_only", False)),
            timeout=self.config.value.policy.max_turn_seconds,
        )

    @staticmethod
    def _origin_snapshot(ctx: Context) -> dict:
        return {
            "actor": ctx.actor,
            "mode": ctx.mode,
            "owner": ctx.owner,
            "group": ctx.group,
            "background": ctx.background,
            "memory_profile": ctx.memory_profile,
            "allowed_tools": ctx.allowed_tools,
            "allowed_skills": ctx.allowed_skills,
            "require_manual_approval": ctx.require_manual_approval,
            "tool_policy": ctx.tool_policy,
        }

    async def start(self, ctx: Context, instance: str, prompt: str) -> dict:
        config = self.instance(instance)
        if len(self.tasks) >= 20:
            raise PublicError("subagent.queueFull")
        key = uid()
        workspace = self.home / "workspaces" / key
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        body = {
            "instance": instance,
            "kind": config.kind,
            "status": "queued",
            "workspace": str(workspace),
            "task": prompt[:16000],
            "round": 0,
        }
        if config.kind == "shiritaibot":
            body["native_session_id"] = key
            body["origin"] = self._origin_snapshot(ctx)
        row = await self.store.put("subagent_run", key, body, ctx.session)
        if self.goals:
            await self.goals.bind(ctx, row)
        self.launch(row, ctx, config, workspace, prompt)
        log.log(Text("subagent.log.queued", instance=instance, kind=config.kind, brief=brief(prompt, 80)), key[:8])
        return row

    async def spawn(
        self,
        ctx: Context,
        prompt: str,
        *,
        role: str = "",
        instructions: str = "",
        tools: list[str] | None = None,
        skills: list[str] | None = None,
        read_only: bool = False,
        model: str = "",
    ) -> dict:
        if not self.config.value.policy.dynamic_subagent:
            raise PublicError("subagent.dynamicDisabled")
        if len(self.tasks) >= 20:
            raise PublicError("subagent.queueFull")
        validated_model = self._validate_dynamic_model(model)
        label = f"dynamic:{role.strip()[:80] or 'worker'}"
        clean_tools = (
            list(dict.fromkeys(item.strip() for item in tools if item.strip()))
            if tools is not None
            else None
        )
        clean_skills = (
            list(dict.fromkeys(item.strip() for item in skills if item.strip()))
            if skills is not None
            else None
        )
        dynamic_spec = {
            "role": role.strip()[:80] or "worker",
            "instructions": instructions.strip()[:16000],
            "model": validated_model,
            "tools": clean_tools,
            "skills": clean_skills,
            "read_only": bool(read_only),
        }
        key = uid()
        workspace = self.home / "workspaces" / key
        await asyncio.to_thread(workspace.mkdir, parents=True, exist_ok=True)
        body = {
            "instance": label,
            "kind": "shiritaibot",
            "dynamic": True,
            "dynamic_spec": dynamic_spec,
            "status": "queued",
            "workspace": str(workspace),
            "task": prompt[:16000],
            "round": 0,
            "native_session_id": key,
            "origin": self._origin_snapshot(ctx),
        }
        spec = self._dynamic_spec(body)
        row = await self.store.put("subagent_run", key, body, ctx.session)
        if self.goals:
            await self.goals.bind(ctx, row)
        self.launch(row, ctx, spec, workspace, prompt)
        log.log(Text("subagent.log.queuedDynamic", label=label, brief=brief(prompt, 80)), key[:8])
        return row

    async def send(self, ctx: Context, key: str, message: str) -> dict:
        """Continue a finished or waiting run once, even with concurrent replies."""
        async with self.resume_lock:
            return await self._send(ctx, key, message)

    async def _send(self, ctx: Context, key: str, message: str) -> dict:
        row = await self.store.get("subagent_run", key)
        # Owner contexts include the Proactive loop that received the needs_input event.
        if not row or (row.get("scope") != ctx.session and not ctx.owner):
            raise PublicError("subagent.taskMissing")
        if key in self.tasks or row["status"] in {"queued", "running"}:
            raise PublicError("subagent.stillRunning")
        if not row.get("native_session_id"):
            raise PublicError("subagent.noResume")
        if len(self.tasks) >= 20:
            raise PublicError("subagent.queueFull")
        if row.get("dynamic"):
            config = self._dynamic_spec(row)
        else:
            config = self.instance(row["instance"])
        if ("kind" in row and row["kind"] != config.kind) or (
            config.kind == "shiritaibot" and "origin" not in row
        ):
            raise PublicError("subagent.kindChanged")
        if config.kind == "shiritaibot":
            origin = Context(row["scope"], **row["origin"])
            if not ctx.owner and (origin.owner or ctx.actor != origin.actor):
                raise PublicError("subagent.resumeOwnerOnly")
            if ctx.allowed_tools is not None:
                allowed = set(ctx.allowed_tools)
                if origin.allowed_tools is not None:
                    allowed.intersection_update(origin.allowed_tools)
                origin = replace(origin, allowed_tools=tuple(sorted(allowed)))
            if ctx.allowed_skills is not None:
                skills = list(ctx.allowed_skills)
                if origin.allowed_skills is not None:
                    skills = [s for s in skills if s in origin.allowed_skills]
                origin = replace(origin, allowed_skills=tuple(dict.fromkeys(skills)))
        else:
            origin = Context(row["scope"], ctx.actor, ctx.mode, ctx.owner, ctx.group)
        if config.kind == "generic":
            raise PublicError("subagent.genericNoResume")
        row.update(status="queued", round=row.get("round", 0) + 1, reply=message[:4000])
        row.pop("result", None)
        row.pop("question", None)
        row.pop("error", None)
        row = await self.store.put("subagent_run", key, row, row["scope"])
        if self.goals and row["scope"] == ctx.session:
            await self.goals.bind(ctx, row)
        self.launch(row, origin, config, Path(row["workspace"]), message, row["native_session_id"])
        log.log(Text("subagent.log.resumed", id=config.id, round=row['round'], brief=brief(message, 80)), key[:8])
        return row

    async def run(
        self, row: dict, ctx: Context, config, workspace: Path, prompt: str, resume=None
    ) -> None:
        key = row["id"]
        try:
            async with self.semaphore:
                row["status"] = "running"
                await self.store.put("subagent_run", key, row, ctx.session)
                log.log(Text("subagent.log.started", id=config.id), key[:8])
                if config.kind == "shiritaibot":
                    if self.native is None:
                        raise PublicError("subagent.runnerMissing")
                    async with asyncio.timeout(config.timeout):
                        summary = await self.native.run(
                            key, ctx, config, workspace, prompt, self.notes() + prompt_text("SUBAGENT_UNATTENDED", marker=NEEDS_INPUT)
                        )
                    question = question_in(summary)
                    row.update(
                        status="needs_input" if question else "completed", result=clip(summary)
                    )
                else:
                    question = await self.run_cli(row, config, workspace, prompt, resume)
                if row["status"] == "needs_input" and question:
                    row["question"] = question[:2000]
                # Raw stderr is deliberately not written into the activity feed.
        except asyncio.CancelledError:
            row["status"] = "cancelled"
            log.log(Text("subagent.log.cancelled", id=config.id), key[:8])
            raise
        except TimeoutError:
            row.update(
                status="failed",
                error="TimeoutError",
                result=t("subagent.timeout", timeout=config.timeout),
            )
            log.warning(Text("subagent.log.timeout", id=config.id), key[:8])
        except Exception as exc:
            row.update(status="failed", error=type(exc).__name__)
            if isinstance(exc, PublicError):
                row["result"] = str(exc)
            log.error(Text("subagent.log.failed", id=config.id, name=type(exc).__name__), key[:8])
        finally:
            status = row["status"]
            if status in {"completed", "failed", "needs_input"} and "error" not in row:
                label = {"completed": t("subagent.status.completed"), "failed": t("subagent.status.failed"), "needs_input": t("subagent.status.needs_input")}[status]
                exit_detail = t("subagent.exitCode", exit_code=row['exit_code']) if "exit_code" in row else ""
                (log.log if status == "completed" else log.warning)(
                    t(
                        "common.labeled",
                        label=f"{config.id} {label}{exit_detail}",
                        value=brief(row.get("result", ""), 100),
                    ),
                    key[:8],
                )
            await self.store.put("subagent_run", key, row, ctx.session)
            if self.tasks.get(key) is asyncio.current_task():
                self.tasks.pop(key, None)
            linked = self.goals and await self.goals.subagent_result(ctx.session, row)
            # Owner-requested work: PE delivers it as a high-priority turn instead of Main polling.
            waiting = status == "needs_input"
            if not linked:
                await self.emit(
                    ExternalEvent(
                        source="subagent",
                        kind="subagent.needs_input" if waiting else "subagent.completed",
                        # One event per round: a resumed run reports again under a new key.
                        dedup_key=f"{key}:{row.get('round', 0)}",
                        title=t("subagent.event.title", id=config.id, value=t("subagent.status.needs_input") if waiting else status),
                        urgency="normal" if status == "cancelled" else "high",
                        payload={
                            "run_id": key,
                            "instance": config.id,
                            "status": status,
                            "session": ctx.session,
                            "task": row.get("task", prompt)[:2000],
                            **({"reply": prompt[:2000]} if resume else {}),
                            "result": row.get("result", "")[-4000:],
                            **({"question": row["question"]} if waiting else {}),
                            **({"error": row["error"]} if row.get("error") else {}),
                        },
                    )
                )

    async def run_cli(self, row, config, workspace, prompt, resume):
        if config.kind == "generic":
            stdin = prompt
        else:
            # Resumed runs already saw the plugin notes in their native session.
            stdin = prompt + ("" if resume else self.notes()) + prompt_text("SUBAGENT_UNATTENDED", marker=NEEDS_INPUT)
        if config.kind == "antigravity":
            stdin = json.dumps({"event": "user", "message": {"content": stdin}}) + "\n"
        parser = OutputParser(config.kind)
        limit = OUTPUT_LIMITS.get(config.kind, OUTPUT_LIMIT)
        result = await run_process(
            command_for(config, workspace, resume),
            workspace,
            stdin,
            config.timeout,
            env=self.child_env(config),
            limit=limit,
            on_line=parser.feed if config.kind in STREAMED else None,
        )
        parsed = parser.finish(result["stdout"])
        notice = ""
        if result["truncated"]:
            notice = (
                prompt_text("SUBAGENT_TRUNCATED", limit=limit)
            )
            if not parser.complete:
                # A raw prefix of events is not a result, and exit code 0 proves nothing.
                parsed.update(error=True, result=t("subagent.outputTooLong"))
        failed = result["exit_code"] != 0 or parsed["error"]
        summary = parsed["result"]
        question = (
            None if config.kind == "generic" else parsed.get("question") or question_in(summary)
        )
        if parsed["denied"]:
            denied = join_list(dict.fromkeys(parsed["denied"]))
            summary += prompt_text("SUBAGENT_DENIED", denied=denied)
            # A denial the agent could not work around means it is stuck, not done.
            question = question or prompt_text("SUBAGENT_DENIED_SUMMARY", denied=denied)
        row.update(
            status="needs_input"
            if question
            and parsed["native"]
            and (not failed or parsed["denied"] or parsed.get("question"))
            else "failed"
            if failed
            else "completed",
            # After question_in, and clipped so that the notice cannot be cut off.
            result=clip(summary, RESULT_LIMIT - len(notice)) + notice,
            native_session_id=parsed["native"] or row.get("native_session_id"),
            exit_code=1 if parsed["error"] and not result["exit_code"] else result["exit_code"],
            truncated=result["truncated"],
        )
        return question

    async def cancel(self, key: str) -> None:
        task = self.tasks.get(key)
        if task:
            # A just-scheduled run must enter its finally block to persist cancellation.
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        await asyncio.gather(*(self.cancel(key) for key in list(self.tasks)))
