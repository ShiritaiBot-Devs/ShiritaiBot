from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path

from shiritaibot.agent import repair_history
from shiritaibot.agent.execution import TurnExecution, TurnLimit, TurnState
from shiritaibot.core import (
    NEEDS_INPUT,
    Context,
    InteractionRequired,
    PublicError,
    canonical,
    delegated_workspace,
    uid,
)
from shiritaibot.prompts import prompt as prompt_text


@dataclass(frozen=True)
class DynamicSubagentSpec:
    id: str
    instructions: str = ""
    model: str = ""
    tools: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    read_only: bool = False
    timeout: int = 1200
    kind: str = "shiritaibot"


class NativeRunner:
    def __init__(self, config, store, llm, registry, skills):
        self.config, self.store, self.llm = config, store, llm
        self.registry, self.skills = registry, skills

    def _resolve_permissions(self, ctx: Context, instance) -> tuple[tuple[str, ...], tuple[str, ...] | None, bool]:
        if isinstance(instance, DynamicSubagentSpec):
            if instance.tools is None:
                allowed = {
                    name
                    for name in self.registry.tools
                    if not name.startswith("subagent_")
                }
            else:
                allowed = {
                    name
                    for name in instance.tools
                    if not name.startswith("subagent_") and name in self.registry.tools
                }
            if instance.skills is None:
                allowed_skills = ctx.allowed_skills
            else:
                skills = tuple(dict.fromkeys(instance.skills))
                if ctx.allowed_skills is not None:
                    skills = tuple(name for name in skills if name in ctx.allowed_skills)
                allowed_skills = skills
                if skills:
                    if instance.tools is not None:
                        allowed.update(
                            name
                            for name in ("skill_list", "skill_read")
                            if name in self.registry.tools
                        )
                else:
                    allowed.discard("skill_list")
                    allowed.discard("skill_read")
            if instance.read_only:
                allowed = {
                    name
                    for name in allowed
                    if (tool := self.registry.tools.get(name)) is not None
                    and tool.risk == "safe"
                }
            if ctx.allowed_tools is not None:
                allowed.intersection_update(ctx.allowed_tools)
            return tuple(sorted(allowed)), allowed_skills, ctx.require_manual_approval

        allowed = {
            name for name in instance.native_tools if not name.startswith(("subagent_", "skill_"))
        }
        if instance.native_skills:
            allowed.update(("skill_list", "skill_read"))
        if ctx.allowed_tools is not None:
            allowed.intersection_update(ctx.allowed_tools)
        return tuple(sorted(allowed)), tuple(instance.native_skills), instance.native_manual_approval

    async def run(
        self, key: str, ctx: Context, instance, workspace: Path, prompt: str, system: str
    ):
        cfg = self.config.value.model_copy(deep=True)
        if instance.model:
            cfg.routes["agent"] = [instance.model]
        execution = TurnExecution(cfg, self.llm, ctx.session, seconds=instance.timeout)
        allowed_tools, allowed_skills, require_manual_approval = self._resolve_permissions(
            ctx, instance
        )
        ctx = replace(
            ctx,
            turn=uid(),
            tool_call_id="",
            subagent_run=key,
            allowed_tools=allowed_tools,
            allowed_skills=allowed_skills,
            require_manual_approval=require_manual_approval,
        )
        system = (
            prompt_text("SUBAGENT_SYSTEM", id=instance.id)
            + system
        )
        if instance.instructions:
            system += "\n\n" + instance.instructions
        if any(t["function"]["name"] == "skill_read" for t in self.registry.specs(ctx)):
            system += await self.skills.prompt(ctx.allowed_skills)

        saved = await self.store.get("subagent_history", key) or {}
        history = repair_history(saved.get("messages", []), None)
        history.append({"role": "user", "content": prompt, "_turn_input": True})
        history = repair_history(history, cfg.policy.max_context_chars)
        start = len(history) - 1

        async def checkpoint(messages=None, summary=None, through=0):
            await self.store.put(
                "subagent_history", key,
                {"messages": history if messages is None else messages,
                 "summary": execution.summary if summary is None else summary,
                 "steps": execution.steps, "status": execution.state},
                ctx.session,
            )

        await checkpoint()
        try:
            result = await self.run_steps(ctx, workspace, system, history, start, execution, checkpoint)
        except TurnLimit as limit:
            try:
                result = await execution.wrap(str(limit), "agent", system, history, repair_history)
            except asyncio.CancelledError:
                execution.state = TurnState.CANCELLED
                raise
            except Exception:
                execution.state = TurnState.FAILED
                raise
        except asyncio.CancelledError:
            execution.state = TurnState.CANCELLED
            raise
        except Exception:
            execution.state = TurnState.FAILED
            raise
        finally:
            history[:] = repair_history(history, None)
            if execution.state == TurnState.BUDGET_STOPPED:
                history.append({"role": "assistant", "content": result})
            await checkpoint()
        return result

    async def run_steps(self, ctx, workspace, system, history, start, execution, checkpoint):
        while True:
            settings = self.config.value
            if not settings.adapter_context_valid(ctx) or (
                not ctx.background and ctx.memory_profile != settings.session_memory(ctx.session)
            ):
                raise PublicError("subagent.sourceChanged")
            await execution.reduce(history, start, "", checkpoint)
            execution.reserve_step()
            reply = await execution.work(
                self.llm.complete, "agent", system, history, self.registry.specs(ctx),
                settings=execution.cfg, scope=ctx.session,
            )
            message = {"role": "assistant", "content": reply.text, "_opaque": reply.opaque}
            if reply.calls:
                message["tool_calls"] = reply.calls
            history.append(message)
            await checkpoint()
            if not reply.calls:
                if not reply.text.strip():
                    raise PublicError("subagent.noResult")
                execution.state = (
                    TurnState.WAITING_INPUT if reply.text.lstrip().startswith(NEEDS_INPUT)
                    else TurnState.COMPLETED
                )
                return reply.text
            for call in reply.calls:
                fn = call["function"]
                token = delegated_workspace.set((ctx.session, workspace))
                try:
                    output = await execution.work(
                        self.registry.execute,
                        replace(ctx, tool_call_id=call["id"]), fn["name"], fn["arguments"]
                    )
                except InteractionRequired as request:
                    history.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": canonical({"waiting": request.reason}),
                        }
                    )
                    execution.state = TurnState.WAITING_INPUT
                    return f"{NEEDS_INPUT} {request.reason}"
                finally:
                    delegated_workspace.reset(token)
                history.append({
                    "role": "tool", "tool_call_id": call["id"],
                    "content": execution.observe_result(fn, output),
                })
                await checkpoint()
                if execution.repeat_count >= 5:
                    raise TurnLimit("repeat")
