from __future__ import annotations

from collections import Counter
from dataclasses import replace
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


class NativeRunner:
    def __init__(self, config, store, llm, registry, skills):
        self.config, self.store, self.llm = config, store, llm
        self.registry, self.skills = registry, skills

    async def run(
        self, key: str, ctx: Context, instance, workspace: Path, prompt: str, system: str
    ):
        cfg = self.config.value.model_copy(deep=True)
        if instance.model:
            cfg.routes["agent"] = [instance.model]
        allowed = {
            name for name in instance.native_tools if not name.startswith(("subagent_", "skill_"))
        }
        if instance.native_skills:
            allowed.update(("skill_list", "skill_read"))
        if ctx.allowed_tools is not None:
            allowed.intersection_update(ctx.allowed_tools)
        ctx = replace(
            ctx,
            turn=uid(),
            tool_call_id="",
            subagent_run=key,
            allowed_tools=tuple(sorted(allowed)),
            allowed_skills=tuple(instance.native_skills),
            require_manual_approval=instance.native_manual_approval,
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
