import asyncio
import hashlib
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from shiritaibot.i18n import Text, t
from shiritaibot.plugin_api.base import (
    ExternalEvent,
    InteractionRequired,
    Plugin,
    PublicError,
    run_process,
    schema,
)

from .protocol import LearnRequest, LearnResponse

AUTH_FAILURES = {
    "auth_required",
    "login_required",
    "session_expired",
    "login_failed",
    "not_configured",
    "verification_required",
    "verification_expired",
    "verification_failed",
    "verification_pending_elsewhere",
    "captcha_required",
    "manual_verification_required",
}


class OneLearnPlugin(Plugin):
    """School-independent tool registration, stdio transport, baseline sync and events.

    School plugins supply their implementation command and login settings.
    No THU URLs, SSO logic or school-specific clients belong in this module.
    """

    def __init__(self, context):
        super().__init__(context)
        self.task = None

    async def start(self):
        self.lock = asyncio.Lock()
        self.context.register_tool(
            "learn_query",
            "learn.tools.query",
            schema(
                {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "courses",
                            "notices",
                            "homework",
                            "files",
                            "homework_attachments",
                            "homework_detail",
                        ],
                    },
                    "course_id": {"type": "string", "maxLength": 128},
                    "homework_id": {"type": "string", "maxLength": 128},
                },
                ["operation"],
            ),
            self.query,
            risk="safe",
            timeout=240,
        )
        self.context.register_tool(
            "learn_download",
            "learn.tools.download",
            schema(
                {
                    "operation": {
                        "type": "string",
                        "enum": ["download", "download_homework_attachment"],
                    },
                    "course_id": {"type": "string", "maxLength": 128},
                    "file_id": {"type": "string", "maxLength": 128},
                    "homework_id": {"type": "string", "maxLength": 128},
                },
                ["operation"],
            ),
            self.query,
            risk="normal",
            timeout=240,
        )
        interval = max(300, int(self.context.settings.get("poll_seconds", 900)))
        self.task = (
            asyncio.create_task(self.poll(interval))
            if self.context.settings.get("poll", False)
            else None
        )

    async def invoke(self, operation, params, owner="owner", *, background=False):
        async with self.lock:
            command = self.context.settings.get("command", [])
            if not command:
                raise PublicError("learn.noCommand")
            password = await self.context.resolve_secret(self.context.settings.get("password", ""))
            request = LearnRequest.model_validate(
                {
                    "version": 1,
                    "operation": operation,
                    "params": params,
                    "owner": owner,
                    "config": {
                        "username": self.context.settings.get("username", ""),
                        "password": password,
                        **({"interactive": False} if background else {}),
                    },
                    "data_dir": str(self.context.data_dir),
                }
            )
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            result = await run_process(
                command,
                self.context.data_dir,
                request.model_dump_json(),
                timeout=230,
                env=env,
                limit=1000000,
            )
            try:
                return LearnResponse.model_validate_json(result["stdout"]).model_dump(
                    exclude_defaults=True
                )
            except ValueError:
                if result["exit_code"]:
                    detail = result["stderr"].strip()[-500:] or t("learn.noStderr")
                    raise PublicError(
                        "learn.adapterExited", exit_code=result['exit_code'], detail=detail
                    ) from None
                if result["truncated"]:
                    raise PublicError("learn.outputTruncated") from None
                raise PublicError("learn.invalidJson") from None

    async def query(self, ctx, args):
        result = await self.invoke(
            args["operation"],
            {k: v for k, v in args.items() if k != "operation"},
            background=ctx.background,
        )
        if ctx.background and not result.get("ok") and result.get("status") in AUTH_FAILURES:
            raise InteractionRequired("auth", t("learn.loginHint"))
        return result

    async def sync_once(self):
        result = await self.invoke("homework", {}, background=True)
        if not result.get("ok"):
            self.context.logger.warning(Text("learn.log.loginState", state=result.get('status', 'unknown')))
            await self.context.emit(
                ExternalEvent(
                    source="learn",
                    kind="learn.auth.expired",
                    dedup_key=f"{self.context.id}:auth:{datetime.now(ZoneInfo('Asia/Shanghai')).date()}",
                    title=t("learn.event.checkLogin"),
                    payload={"status": result.get("status", "unknown")},
                )
            )
            return
        snapshot = self.context.data_dir / "assignments.json"
        old = None
        if await asyncio.to_thread(snapshot.exists):
            old = json.loads(await asyncio.to_thread(snapshot.read_text, encoding="utf-8"))
        current = {}
        for item in result["data"]:
            digest = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            key = str(item.get("homework_id", digest))
            current[key] = digest
            # The first successful sync is only a baseline, never an announcement flood.
            if old is not None and old.get(key) != digest:
                await self.context.emit(
                    ExternalEvent(
                        source="learn",
                        kind="learn.assignment.updated"
                        if key in old
                        else "learn.assignment.created",
                        dedup_key=key + ":" + digest,
                        title=str(item.get("title", t("learn.event.homeworkChanged"))),
                        payload=item,
                    )
                )

        def save():
            temp = snapshot.with_suffix(".tmp")
            temp.write_text(json.dumps(current), encoding="utf-8")
            temp.replace(snapshot)

        await asyncio.to_thread(save)
        changed = sum(
            1 for key, digest in current.items() if old is not None and old.get(key) != digest
        )
        self.context.logger.log(
            t("learn.synced", count=len(current)) + (t("learn.syncedChanged", changed=changed) if changed else "")
            if old is not None
            else t("learn.firstSync", count=len(current))
        )

    async def poll(self, interval):
        while True:
            try:
                await self.sync_once()
            except Exception as exc:
                self.context.logger.warning(Text("learn.log.syncFailed", name=type(exc).__name__))
                await self.context.emit(
                    ExternalEvent(
                        source="learn",
                        kind="learn.sync.failed",
                        dedup_key="sync:" + type(exc).__name__,
                        title=t("learn.event.syncFailed"),
                        payload={"error": type(exc).__name__},
                    )
                )
            await asyncio.sleep(interval)

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
