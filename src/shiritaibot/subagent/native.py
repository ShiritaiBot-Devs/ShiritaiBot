from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

from shiritaibot.agent import repair_history
from shiritaibot.agent.context import context_chars
from shiritaibot.core import (
    Context,
    InteractionRequired,
    PublicError,
    canonical,
    delegated_workspace,
    fingerprint,
    uid,
)
from shiritaibot.subagent import NEEDS_INPUT


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
            f"你是 ShiritaiBot 的子代理 {instance.id}。请完成委派的任务并向主 Agent 汇报结果。"
            + system
        )
        if instance.instructions:
            system += "\n\n" + instance.instructions
        if any(t["function"]["name"] == "skill_read" for t in self.registry.specs(ctx)):
            system += await self.skills.prompt(ctx.allowed_skills)

        saved = await self.store.get("subagent_history", key) or {}
        history = repair_history(saved.get("messages", []), None)
        history.append({"role": "user", "content": prompt})
        history = repair_history(history, cfg.policy.max_context_chars)

        async def checkpoint():
            await self.store.put("subagent_history", key, {"messages": history}, ctx.session)

        await checkpoint()
        repeats = Counter()
        for _ in range(cfg.policy.max_steps):
            settings = self.config.value
            if not settings.adapter_context_valid(ctx) or (
                not ctx.background and ctx.memory_profile != settings.session_memory(ctx.session)
            ):
                raise PublicError("来源会话绑定已变更")
            if context_chars(history) > cfg.policy.max_context_chars * 2:
                raise PublicError("子代理上下文达到上限")
            reply = await self.llm.complete(
                "agent", system, history, self.registry.specs(ctx), settings=cfg, scope=ctx.session
            )
            message = {"role": "assistant", "content": reply.text, "_opaque": reply.opaque}
            if reply.calls:
                message["tool_calls"] = reply.calls
            history.append(message)
            await checkpoint()
            if not reply.calls:
                if not reply.text.strip():
                    raise PublicError("模型未返回子代理任务结果")
                return reply.text
            for call in reply.calls:
                fn = call["function"]
                signature = fingerprint(fn)
                repeats[signature] += 1
                if repeats[signature] >= 5:
                    raise PublicError("子代理重复调用同一工具已达上限")
                token = delegated_workspace.set((ctx.session, workspace))
                try:
                    output = await self.registry.execute(
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
                    await checkpoint()
                    return f"{NEEDS_INPUT} {request.reason}"
                finally:
                    delegated_workspace.reset(token)
                history.append({"role": "tool", "tool_call_id": call["id"], "content": output})
                await checkpoint()
        raise PublicError(f"子代理已达到本轮步数限制（{cfg.policy.max_steps} 步）")
