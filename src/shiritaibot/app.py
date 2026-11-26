from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path

from shiritaibot.adapters.onebot11 import AdapterHub
from shiritaibot.agent import AgentRuntime
from shiritaibot.auth import Permissions, Vault
from shiritaibot.config import (
    BUILTIN_MEMORY_PROFILES,
    AdapterConfig,
    ConfigRevisionConflict,
    ConfigService,
    Settings,
    is_feishu_session,
    is_qq_official_session,
)
from shiritaibot.core import EventBus, PublicError
from shiritaibot.core.logs import describe_session, get_logger
from shiritaibot.emotion import EmotionManager
from shiritaibot.heartflow import Heartflow
from shiritaibot.i18n import Text, t
from shiritaibot.knowledge import KnowledgeBase
from shiritaibot.llm import ProviderManager
from shiritaibot.llm.subscription import PROTOCOL as SUBSCRIPTION_PROTOCOL
from shiritaibot.mcp import McpManager
from shiritaibot.meme import MemeManager
from shiritaibot.memory import MemoryManager, ShortTermMemory
from shiritaibot.memory.extract import MemoryExtractor
from shiritaibot.memory.maintenance import MemoryMaintenance
from shiritaibot.plugin.core import PluginManager
from shiritaibot.proactive import PREPARE_SESSION, ProactiveEngine
from shiritaibot.routine import RoutineManager
from shiritaibot.skill import SkillManager
from shiritaibot.store import Store
from shiritaibot.subagent import SubagentManager
from shiritaibot.subagent.native import NativeRunner
from shiritaibot.tools import ToolRegistry
from shiritaibot.tools.builtin import register_builtin

log = get_logger("ShiritaiBot")

ROUTE_LABELS = {
    "chat": "app.routes.chat",
    "agent": "app.routes.agent",
    "agent_chat": "app.routes.agent_chat",
    "router": "app.routes.router",
    "light": "app.routes.light",
    "memory": "app.routes.memory",
    "approval": "app.routes.approval",
    "embedding": "app.routes.embedding",
    "vision": "app.routes.vision",
    "meme": "app.routes.meme",
}


class FeishuAdapterConflict(PublicError):
    pass


class Application:
    def __init__(self, home: Path, *, desktop: bool = False):
        self.home = home.resolve()
        self.desktop = desktop
        self.bus = EventBus()
        self.config = ConfigService(self.home)
        self.store = Store(self.home, self.bus)
        self.tasks: list[asyncio.Task] = []
        self.configure_lock = asyncio.Lock()
        self.memories: dict[str, MemoryManager] = {}
        self.knowledge_bases: dict[str, KnowledgeBase] = {}

    async def start(self) -> None:
        log.log(Text("app.log.starting", home=self.home))
        await self.config.load()
        await self.store.open()
        self.vault = Vault(self.home, self.store)
        await self.vault.open()
        protected = await self.vault.protect_settings(self.config.value)
        if protected != self.config.value:
            await self.config.save(protected)
        self.permissions = Permissions(self.config, self.store)
        self.registry = ToolRegistry(self.permissions, self.store)
        await self.refresh_secret_list()
        self.llm = ProviderManager(self.config, self.vault, self.store)
        self.permissions.llm = self.llm
        self.memory = MemoryManager(self.store, self.config, self.llm)
        self.permissions.memory_for = self.memory_for
        await self.memory.open()
        self.memories["default"] = self.memory
        await self.open_memory_profiles(self.config.value)
        self.short_term = ShortTermMemory(self.store, self.llm, config=self.config)
        await self.short_term.recover()
        self.memes = MemeManager(self.store, self.config, self.llm)
        await self.memes.open()
        self.knowledge = KnowledgeBase(self.store, self.config, self.llm)
        await self.knowledge.open()
        self.knowledge_bases["default"] = self.knowledge
        await self.open_knowledge_libraries(self.config.value)
        self.adapters = AdapterHub(self.config, self.vault, self.store)
        self.adapters.memes = self.memes
        self.proactive = ProactiveEngine(
            self.config,
            self.store,
            self.llm,
            self.adapters.notify_owner,
            self.short_term,
            notify_session=self.notify_session,
        )
        self.emotion = EmotionManager(self.store, self.llm, self.config, self.proactive.emit)
        # Handled events feed long-term memory; the plugin that emitted them is not involved.
        self.extractor = MemoryExtractor(self.memory, self.store, self.config, self.llm)
        await self.extractor.open()
        self.proactive.memory_sink = self.extractor.enqueue_event
        self.adapters.emit = self.proactive.emit
        self.maintenance = MemoryMaintenance(self.memory, self.store, self.proactive.emit)
        self.routine = RoutineManager(self.store, self.proactive.emit)
        self.skills = SkillManager(self.home, self.config)
        self.subagents = SubagentManager(
            self.config,
            self.store,
            self.home,
            self.proactive.emit,
            NativeRunner(self.config, self.store, self.llm, self.registry, self.skills),
        )
        self.plugins = PluginManager(
            self.home,
            self.config,
            self.registry,
            self.vault,
            self.store,
            self.proactive.emit,
            desktop=self.desktop,
        )
        self.plugins.llm, self.plugins.adapters = self.llm, self.adapters
        self.plugins.hooks.send = self.adapters.enqueue
        self.adapters.hooks = self.proactive.hooks = self.plugins.hooks
        self.subagents.env = self.plugins.child_env
        self.mcp = McpManager(self.config, self.registry, self.vault, self.store)
        self.subagents.notes = self.plugins.subagent_notes
        await register_builtin(self)
        self.runtime = AgentRuntime(
            self.config,
            self.store,
            self.llm,
            self.registry,
            self.memory_for,
            self.deliver,
            self.short_term,
            emotion=self.emotion,
            skills=self.skills,
        )
        self.runtime.hooks = self.plugins.hooks
        self.subagents.goals = self.runtime.goals
        await self.runtime.goals.recover()
        self.adapters.runtime, self.adapters.permissions = self.runtime, self.permissions
        self.adapters.heartflow = Heartflow(self.runtime, self.adapters.enqueue)
        self.runtime.deliver_group_reply = self.adapters.deliver_group_reply
        self.permissions.notify = self.notify_approval
        self.proactive.notify_interaction = partial(self.adapters.notify_owner, handoff=False)
        self.runtime.wait_for_interaction = self.proactive.wait_for_interaction
        self.plugins.resolve_interaction = self.proactive.resolve_interaction
        self.proactive.runtime = self.runtime
        self.proactive.channels = self.plugins.notifiers
        self.plugins.get_event = self.proactive.get_event
        await self.plugins.start()
        await self.mcp.start()
        await self.adapters.start()
        self.tasks += [
            asyncio.create_task(self.runtime.goals.watch()),
            asyncio.create_task(self.routine.run()),
            asyncio.create_task(self.proactive.run()),
            asyncio.create_task(self.short_term.run()),
            asyncio.create_task(self.memory.run()),
            asyncio.create_task(self.memes.run()),
            asyncio.create_task(self.knowledge.run()),
            asyncio.create_task(self.extractor.run()),
            asyncio.create_task(self.maintenance.run()),
        ]
        await self.plugins.mark_ready()
        cfg = self.config.value
        log.log(
            Text(
                "app.log.startedOn" if cfg.proactive.enabled else "app.log.startedOff",
                tools=Text("app.log.tools", count=len(self.registry.tools)),
                plugins=Text("app.log.plugins", count=len(self.plugins.loaded)),
                adapters=Text("app.log.connections", count=len(self.adapters.instances)),
                mcp=Text("app.log.mcpServers", count=len(self.mcp.servers)),
            )
        )

    def memory_for(self, profile: str) -> MemoryManager:
        memory = self.memories.get(profile)
        if memory is None:
            raise PublicError("app.memoryProfileMissing")
        return memory

    def knowledge_for(self, library: str) -> KnowledgeBase:
        if library != "default" and library not in self.config.value.knowledge.libraries:
            raise PublicError("app.libraryMissing")
        return self.knowledge_bases[library]

    async def open_knowledge_libraries(self, settings: Settings) -> None:
        for library in settings.knowledge.libraries.keys() - self.knowledge_bases.keys():
            store = Store(self.home / "knowledge-libraries" / f"library-{library}", self.bus)
            try:
                await store.open()
                knowledge = KnowledgeBase(store, self.config, self.llm, library=library)
                await knowledge.open()
            except BaseException:
                if hasattr(store, "db"):
                    await store.close()
                raise
            self.knowledge_bases[library] = knowledge
            self.tasks.append(asyncio.create_task(knowledge.run()))

    async def open_memory_profiles(self, settings: Settings) -> None:
        profiles = dict(settings.memory.profiles)
        feishu = self.home / "memory-profiles" / "feishu" / "state.sqlite3"
        if (
            any(a.type == "feishu" and a.memory_profile == "feishu" for a in settings.adapters)
            or "feishu" in settings.memory.sessions.values()
            or await asyncio.to_thread(feishu.is_file)
        ):
            profiles["feishu"] = BUILTIN_MEMORY_PROFILES["feishu"]
        for profile_id, profile in profiles.items():
            memory = self.memories.get(profile_id)
            if memory is not None:
                store = memory.store
            else:
                folder = self.home / "memory-profiles" / profile_id
                await asyncio.to_thread(folder.mkdir, mode=0o700, parents=True, exist_ok=True)
                store = Store(folder, self.bus)
            try:
                if memory is None:
                    await store.open()
                saved = await store.get("memory_profile", "settings")
                if saved and saved["audience"] != profile.audience:
                    raise PublicError("app.memoryAudienceFixed", profile_id=profile_id)
                if memory is not None:
                    continue
                if not saved:
                    await store.put("memory_profile", "settings", {"audience": profile.audience})
                memory = MemoryManager(store, self.config, self.llm, history_store=self.store)
                await memory.open()
            except BaseException:
                if profile_id not in self.memories and hasattr(store, "db"):
                    await store.close()
                raise
            self.memories[profile_id] = memory
            extractor = MemoryExtractor(
                memory,
                self.store,
                self.config,
                self.llm,
                profile=profile_id,
                shared=profile.audience == "shared",
            )
            maintenance = MemoryMaintenance(memory, store, None)
            self.tasks.extend(
                [
                    asyncio.create_task(memory.run()),
                    asyncio.create_task(extractor.run()),
                    asyncio.create_task(maintenance.run()),
                ]
            )

    async def deliver(self, session: str, text: str, *, image: bytes | None = None) -> None:
        if session.startswith(PREPARE_SESSION):
            # Proactive turns return to PE, which owns the final notification decision.
            await self.proactive.prepared(session, text)
        elif session.startswith("background:"):
            await self.adapters.notify_owner(text)
        else:
            await self.adapters.enqueue(session, text, image=image)

    async def notify_session(self, session: str, text: str) -> None:
        if is_qq_official_session(session):
            raise PublicError("app.qqReplyOnly")
        if is_feishu_session(session) and not self.config.value.active_feishu_session(session):
            raise PublicError("app.routineUnbound")
        await self.runtime.handoff(session, text, self.adapters.enqueue)

    async def notify_approval(
        self, text: str, *, session: str | None = None, batch_id: str = ""
    ) -> None:
        target = session or self.permissions.approval_target()
        if self.permissions.approval_target_current(target):
            # Prompts are immediate and skip plugin text decorators.
            await self.adapters.enqueue(target, text, decorate=False, approval_batch=batch_id)

    async def diagnostics(self) -> list[str]:
        """Warnings about configuration that would silently fail at runtime."""
        settings = self.config.value
        warnings: list[str] = []

        async def resolves(ref: str) -> bool:
            try:
                return bool(await self.vault.resolve(ref))
            except Exception:
                return False

        provider_ok: dict[str, bool] = {}
        for provider in settings.providers:
            if provider.protocol == SUBSCRIPTION_PROTOCOL:
                status = await self.llm.subscriptions.status(provider.id)
                connected = status["status"] == "connected"
                provider_ok[provider.id] = provider.enabled and connected
                if provider.enabled and not connected and any(
                    model.enabled and model.provider == provider.id for model in settings.models
                ):
                    warnings.append(t("app.needsChatgpt", id=provider.id))
                continue
            provider_ok[provider.id] = provider.enabled and await resolves(provider.api_key)
            if provider.enabled and not provider_ok[provider.id]:
                warnings.append(t("app.noApiKey", id=provider.id))
        models = {model.id: model for model in settings.models}
        routes = [(route, refs, "") for route, refs in settings.routes.items()]
        routes.extend(
            (route, refs, f"{describe_session(session)} · ")
            for session, overrides in settings.session_routes.items()
            for route, refs in overrides.items()
        )
        for route, refs, prefix in routes:
            if route == "test":
                continue
            label = prefix + t(ROUTE_LABELS.get(route, route))
            if route == "embedding" and not any(m.kind == "embedding" for m in models.values()):
                continue
            if not refs:
                warnings.append(t("app.routeEmpty", label=label))
                continue
            usable = [
                model
                for model in refs
                if (cfg_model := models.get(model)) is not None
                and cfg_model.enabled
                and provider_ok.get(cfg_model.provider, False)
            ]
            if not usable:
                warnings.append(t("app.routeUnavailable", label=label))
        for provider_id, count in self.llm.failing().items():
            warnings.append(
                t("app.providerFailing", provider_id=provider_id, count=count)
            )
        search = settings.web_search
        if search.enabled:
            provider = getattr(search, search.provider, None)
            if provider is None or not await resolves(provider.api_key):
                warnings.append(
                    t("app.searchNoKey", provider=search.provider)
                )
        return warnings

    async def refresh_secret_list(self) -> None:
        """Cache decrypted credentials so tool output can be scrubbed of them."""
        values = []
        for row in await self.store.list("secret"):
            try:
                value = self.vault.cipher.decrypt(row["cipher"].encode()).decode()
            except Exception:
                continue
            if len(value) >= 6:
                values.append(value)
        self.registry.secrets = tuple(values)

    async def reconfigure(self, settings: Settings, *, expected_version: int | None = None) -> None:
        async with self.configure_lock:
            if expected_version is not None and self.config.version != expected_version:
                raise ConfigRevisionConflict
            await self._reconfigure_locked(settings, expected_version=expected_version)

    async def add_feishu_adapter(self, adapter: AdapterConfig) -> int:
        async with self.configure_lock:
            current = self.config.value
            for existing in current.adapters:
                if existing.id == adapter.id:
                    raise FeishuAdapterConflict(t("app.feishuNameTaken"))
                if existing.type == "feishu":
                    if existing.feishu_binding == adapter.feishu_binding:
                        raise FeishuAdapterConflict(t("app.feishuAppTaken"))
                    if existing.enabled and adapter.enabled:
                        raise FeishuAdapterConflict(t("app.feishuEnabled"))
            data = current.model_dump()
            data["adapters"].append(adapter.model_dump())
            await self._reconfigure_locked(
                Settings.model_validate(data), restart_adapters=adapter.enabled
            )
            return self.config.version

    async def _reconfigure_locked(
        self,
        settings: Settings,
        *,
        restart_adapters: bool = True,
        expected_version: int | None = None,
    ) -> None:
        log.log("app.log.reloading")
        previous = self.config.value
        settings = settings.model_copy(deep=True)
        saved_secrets = {
            (item.type, item.id, item.app_id, item.domain): item.app_secret
            for item in previous.adapters
            if item.type in {"feishu", "qq_official"}
        }
        for item in settings.adapters:
            if item.type in {"feishu", "qq_official"} and not item.app_secret:
                item.app_secret = saved_secrets.get(
                    (item.type, item.id, item.app_id, item.domain), ""
                )
        settings = await self.vault.protect_settings(settings, await self.plugins.schemas())
        await self.open_memory_profiles(settings)
        await self.open_knowledge_libraries(settings)
        await self.config.save(settings, expected_version=expected_version)
        subscription_ids = {
            provider.id
            for provider in settings.providers
            if provider.protocol == SUBSCRIPTION_PROTOCOL
        }
        for provider in previous.providers:
            if provider.protocol == SUBSCRIPTION_PROTOCOL and provider.id not in subscription_ids:
                if not await self.llm.subscriptions.disconnect(provider, remove=True):
                    log.warning("app.log.chatgptRemoved")
        flow = self.adapters.heartflow
        if flow:
            await flow.reset(
                [
                    session
                    for session in flow.groups
                    if not flow.enabled(session)
                    or previous.session_memory(session) != settings.session_memory(session)
                ]
            )
        for library in settings.knowledge.libraries:
            self.knowledge_bases[library].wake.set()
        await self.runtime.cancel_reconfigured_sessions(previous)
        await self.permissions.menu.reconfigured()
        await self.refresh_secret_list()
        if restart_adapters and settings.adapters != previous.adapters:
            await self.adapters.close()
            await self.adapters.start()
        else:
            log.log("app.log.keepAdapters")
        await register_builtin(self)
        await self.plugins.reload_all()
        await self.mcp.reload()

    async def close(self) -> None:
        log.log("app.log.stopping")
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.runtime.close()
        await self.subagents.close()
        await self.plugins.close()
        await self.mcp.close()
        await self.adapters.close()
        await self.permissions.menu.close()
        await self.llm.close()
        for profile_id, memory in self.memories.items():
            if profile_id != "default":
                await memory.store.close()
        for library, knowledge in self.knowledge_bases.items():
            if library != "default":
                await knowledge.store.close()
        await self.store.close()
        log.log("app.log.stopped")
