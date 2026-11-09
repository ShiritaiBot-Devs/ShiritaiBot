# 审批交互重设计 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把人工审批从「复制 10 位 id」改成「看两行说明 + 回一个数字」，支持批量、按类分组、五分钟免问与永久免问。

**Architecture:** 新增 `auth/menu.py`，用一个 `ApprovalMenu` 对象持有「收集窗口 → 批次 → 菜单阶段（list / detail / single）」的内存状态，并负责渲染文案、解析数字回复。`Permissions.check()` 在原本要人工审批的位置把请求交给菜单，等待原有 `future`；免问授权与查询撤销放在 `Permissions` 上（它已经持有 config/store）。QQ 适配器只做一件事：把主人私聊里的合法数字转给菜单。

**Tech Stack:** Python 3.14、asyncio、pydantic、pytest / pytest-asyncio、SQLite store、OneBot 11。

**Spec:** `docs/superpowers/specs/2026-11-09-approval-interaction-design.md`

## Global Constraints

- **不要执行 `git commit` 或 `git push`**：用户自己提交。每个任务结束时跑测试、停在绿色，改动留在工作区。
- 所有面向用户的文案用简体中文，数字选项固定为半角 `1`–`4`。
- WebUI 审批接口 `POST /api/approvals/{key}` 不改；只有 QQ 私聊里出现数字菜单。
- 只有主人在私聊里能审批；群聊、网页、他人私聊各自独立。
- 含密钥文件的 shell 命令（`SENSITIVE_FILE` 命中）永远只能一次一问：不提供 3/4 选项，收到 3/4 也不得产生授权。
- `background:*` 会话的审批只给「同意 / 拒绝」。
- 审批超时沿用 `policy.approval_timeout`（默认 600 秒）；超时或已被 WebUI 处理掉的请求不得再出现在菜单里，也不得被执行。
- 每屏最多列 5 条请求；多余的进排队区，当前批次关闭后再发。
- 测试命令：`.venv\Scripts\python -m pytest`（单项测试加 `-k` 或文件::用例名）。

## Review Focus

- 菜单打开时，主人发来的普通消息（包括以数字开头的话）不能被吞；只有当前阶段合法的数字才拦截。
- 同一聊天不能同时出现两套数字菜单：批次未关闭时新请求必须排队，关闭后才发下一批。
- 含密钥文件的 shell 命令：菜单里不出现 3/4，且即使收到 `3`/`4` 也不得免问。
- 群/网页/后台会话的审批不显示 3/4；免问授权不得跨会话生效。
- 菜单发出前请求就已超时或被 WebUI 处理：窗口关闭时不再发提示，也不留下阻塞后续请求的死批次。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `src/shiritaibot/auth/menu.py`（新建） | 收集窗口、批次状态、渲染文案、数字解析、审批说明生成、敏感 shell 判定 `SENSITIVE_FILE` |
| `src/shiritaibot/auth/__init__.py`（修改） | `check()` 接入菜单；五分钟免问表；永久授权（策略 + 记录 + 记忆）；`/grants` 的数据接口 |
| `src/shiritaibot/adapters/onebot11.py`（修改） | 主人私聊数字回复转给菜单；唤醒词剥离与 `control` 判定包含菜单选项 |
| `src/shiritaibot/agent/commands.py`（修改） | `/grants`、`/grants 撤销 <工具>` |
| `src/shiritaibot/app.py`（修改） | 菜单通知目标、记忆管理器注入、关闭时取消菜单任务 |
| `tests/test_approval_menu.py`（新建） | 菜单与授权的全部行为测试 |
| `tests/test_permissions.py`（修改） | 五分钟免问命中/不命中、敏感命令不可免问的底层断言 |

---

### Task 1: 审批说明生成与单条菜单

**Files:**
- Create: `src/shiritaibot/auth/menu.py`
- Modify: `src/shiritaibot/auth/__init__.py`（`check()` 的人工审批分支、`__init__`、`__init__.py` 顶部导入）
- Modify: `src/shiritaibot/app.py:64-68`（菜单通知目标）
- Test: `tests/test_approval_menu.py`

**Interfaces:**
- Consumes: `Permissions.notify: Callable[[str], Awaitable[None]] | None`、`Permissions.llm: ProviderManager | None`、`Permissions.store`、`Permissions.config`；`ProviderManager.complete(route, system, messages, tools, settings=None, scope=None) -> Reply`。
- Produces:
  - `SENSITIVE_FILE: re.Pattern` 移到 `auth/menu.py`（`auth/__init__.py` 从那里导入）。
  - `class ApprovalMenu: def __init__(self, permissions); target: str; async def submit(self, ctx: Context, tool: Tool, args: dict, key: str, expires: float) -> None; async def close(self) -> None`
  - `Permissions.menu: ApprovalMenu`（在 `Permissions.__init__` 里创建）。
  - 审批记录新增字段：`risk`（`tool.risk`）、`reason`（生成的两行说明，初始 `""`）、`batch`（批次 id，初始 `""`）。

- [ ] **Step 1: 写失败测试**

新建 `tests/test_approval_menu.py`，辅助函数固定为下面这几个：

```python
import asyncio
import json

import pytest

from shiritaibot.core import Context, PublicError, Reply, Tool
from shiritaibot.tools import schema

OWNER = Context("private:qq:1", "owner", "agent", True)


async def eventually(check, timeout: float = 3):
    async with asyncio.timeout(timeout):
        while not (result := await check()):
            await asyncio.sleep(0.01)
        return result


async def wait_text(app, needle="回数字"):
    async def find():
        for row in await app.store.list("outbox"):
            if needle in row["text"]:
                return row["text"]
        return None

    return await eventually(find)


async def no_text(app, needle, wait=0.8):
    await asyncio.sleep(wait)
    return not any(needle in row["text"] for row in await app.store.list("outbox"))


async def pending(app, name, risk="dangerous", args=None, ctx=OWNER):
    async def handler(context, arguments):
        return {"ok": True}

    app.registry.add(Tool(name, "", schema({}), handler, risk=risk))
    task = asyncio.create_task(app.registry.execute(ctx, name, args or {}))

    async def has_row():
        rows = await app.store.list("approval", scope=ctx.session)
        return next((row for row in rows if row["tool"] == name), None)

    await eventually(has_row)
    return task


@pytest.fixture
def window(app, monkeypatch):
    import shiritaibot.auth.menu as menu

    monkeypatch.setattr(menu, "WINDOW", 0.5)
    monkeypatch.setattr(menu, "REASON_BUDGET", 0.05)
    app.permissions.menu.target = "web:owner"
    return app
```

本任务的三个用例：

```python
async def test_single_request_menu_has_four_options(window):
    task = await pending(window, "danger_one")
    try:
        text = await wait_text(window)
        for line in [
            "需要确认：danger_one",
            "1 同意",
            "2 拒绝",
            "3 五分钟内 danger_one 都同意",
            "4 以后 danger_one 都同意",
        ]:
            assert line in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reason_falls_back_when_model_unavailable(window):
    task = await pending(window, "danger_one", args={"value": "x"})
    try:
        text = await wait_text(window)
        assert "要做：" in text
        assert "为什么：" in text
        assert "说明生成失败" in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_reason_comes_from_light_model(window, monkeypatch):
    from unittest.mock import AsyncMock

    monkeypatch.setattr(
        window.llm, "complete", AsyncMock(return_value=Reply(text="要做：查看状态\n为什么：你要确认"))
    )
    task = await pending(window, "danger_one")
    try:
        text = await wait_text(window)
        assert "要做：查看状态" in text
        assert "为什么：你要确认" in text
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -x -q`
Expected: FAIL，`AttributeError: 'Permissions' object has no attribute 'menu'` 或导入失败。

- [ ] **Step 3: 建 `src/shiritaibot/auth/menu.py`**

必须存在的模块级名字与精确签名：

```python
WINDOW = 2.0          # 收集窗口
REASON_BUDGET = 2.0   # 说明生成预算
QUICK_SECONDS = 300   # 五分钟免问
PAGE_SIZE = 5
RISK_LABELS = {"safe": "安全", "normal": "一般", "sensitive": "敏感", "dangerous": "危险"}
RISK_ORDER = {"dangerous": 0, "sensitive": 1, "normal": 2, "safe": 3}
SENSITIVE_FILE = re.compile(
    r"config\.toml|master\.key|web\.token|\.credentials|state\.sqlite3|profiles[\\/]", re.I
)

@dataclass
class Request:
    key: str
    ctx: Context
    tool: Tool
    args: dict
    reason: str = ""
    reason_task: asyncio.Task | None = None

@dataclass
class Batch:
    id: str
    requests: list[Request]
    stage: str = "list"          # "list" | "detail" | "single"
    settled: set[str] = field(default_factory=set)

class ApprovalMenu:
    target: str = "web:owner"
    def __init__(self, permissions): ...
    async def submit(self, ctx, tool, args, key, expires) -> None
    async def close(self) -> None
```

内部约定（实现者按此写，别自己另起名字）：
- `self.permissions`、`self.batch: Batch | None`、`self.queue: list[Request]`、`self.window: list[Request]`、`self.expires: dict[str, float]`、`self.tasks: set[asyncio.Task]`。
- `submit()`：建 `Request`，`req.reason_task = self._spawn(self._describe(req))`；`self.batch` 存在就 `self.queue.append(req)`，否则进 `self.window`，并在第一次时 `self._spawn(self._wait_window())`。
- `_wait_window()`：`await asyncio.sleep(WINDOW)`；把 `self.window` 换出，逐个 `await self._is_pending(req)` 过滤；为空直接返回；`self.batch` 仍为空则 `await self._open(entries[:PAGE_SIZE])` 且其余进 `self.queue`，否则全部进 `self.queue`。
- `_open(requests)`：建 `Batch(uid()[:10], requests)`；对每条把 `approval` 记录的 `batch` 写成批次 id（`store.get` → 改 `batch` → `store.put`）；条数为 1 时 `stage="single"` 并 `await self._show_single(requests)`，否则 `stage="list"` 并 `await self._send(self._list_text(requests))`。`_list_text` 本任务先返回单条菜单文案（多条在 Task 2 补）。
- `_show_single(requests)`：取 `requests[0]`，`await self._await_reason(req)` 后 `await self._send(self._single_text(req))`。
- `_describe(req)`：见下。
- `_send(text)`：`if self.permissions.notify: await self.permissions.notify(text)`。
- `_spawn(coro) -> asyncio.Task`：创建任务、放进 `self.tasks`、完成后 `discard`。
- `close()`：取消 `self.tasks` 并 `gather(return_exceptions=True)`。

`_single_text(req) -> str` 输出顺序固定：

```
需要确认：{tool.name}
{reason 的两行}
参数：{brief(canonical(args), 300)}
回数字：
1 同意
2 拒绝
3 五分钟内 {tool.name} 都同意
4 以后 {tool.name} 都同意，不再问
```

风险为 `sensitive` / `dangerous` 时，第 4 行末尾加 `（⚠ 高危，慎重）`。

`_describe(req)`：预算 `REASON_BUDGET`，路由 `light`。系统提示固定为：

```
你在为主人撰写工具调用的审批说明。只输出两行纯文本，不要解释：
要做：一句话说明这次调用打算做什么
为什么：一句话说明为什么需要做（结合对话）
每行不超过40字。
```

用户消息用 `canonical({...})`，字段：`tool`、`description`、`risk`、`arguments`，加最近 6 条对话（`store.history(session, limit=6, include_commands=False)`，每条 `brief(content, 800)`）。模型返回文本取前两个非空行；第一行不以 `要做` 开头就视为失败。失败/超时/`llm is None` 一律降级：

```python
f"要做：{req.tool.name} {brief(canonical(req.args), 160)}\n为什么：（说明生成失败，请按上面的参数判断）"
```

- [ ] **Step 4: 接进 `Permissions`**

- `auth/__init__.py` 顶部：`from shiritaibot.auth.menu import SENSITIVE_FILE, ApprovalMenu`，删掉本文件里的 `SENSITIVE_FILE` 定义。
- `Permissions.__init__` 末尾：`self.menu = ApprovalMenu(self)`。
- `check()` 人工分支：记录体加 `"risk": tool.risk, "reason": "", "batch": ""`；把原来的 `self.notify(...)` 调用整段换成 `await self.menu.submit(ctx, tool, args, key, body["expires"])`。超时分支写 `expired` 后加 `self.menu.settled(key)`（`settled` 本任务先做成空方法，Task 2 填）。
- `app.py` 在 `self.permissions = Permissions(...)` 之后：

```python
owner = next((a for a in self.config.value.adapters if a.enabled and a.owner_id), None)
self.permissions.menu.target = (
    f"private:{owner.id}:{owner.owner_id}" if owner else "web:owner"
)
```

- `app.close()` 在 `self.store.close()` 之前加 `await self.permissions.menu.close()`。

- [ ] **Step 5: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q`
Expected: 3 passed。再跑 `tests/test_permissions.py`，Expected: 全过（原有 `同意`/`拒绝` 路径不受影响）。

---

### Task 2: 多条汇总清单（list 阶段）

**Files:**
- Modify: `src/shiritaibot/auth/menu.py`
- Test: `tests/test_approval_menu.py`

**Interfaces:**
- Consumes: Task 1 的 `Request`、`Batch`、`submit`、`_open`、`_send`。
- Produces:
  - `ApprovalMenu.accepts(self, session: str, text: str) -> bool`
  - `ApprovalMenu.reply(self, session: str, text: str) -> bool`（返回 `True` 表示已处理且所有回复文案已由菜单自己发出）
  - `ApprovalMenu.settled(self, key: str) -> None`
  - `_list_text(requests) -> str`、`_detail_text(groups) -> str`、`_groups(requests) -> list[tuple[str, str, list[Request]]]`
  - `_resolve(req, allow) -> None`（内部：`try: await permissions.resolve(...) except PublicError: pass` 并写日志）

- [ ] **Step 1: 写失败测试**

```python
async def test_two_requests_share_one_list_and_approve_all(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    text = await wait_text(window, "有 2 条待确认")
    assert "1 全部同意" in text
    assert "2 全部拒绝" in text
    assert "3 看详情" in text
    assert await window.permissions.menu.reply("web:owner", "1") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    assert json.loads(await asyncio.wait_for(second, 3))["ok"] is True


async def test_list_deny_all(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    await wait_text(window, "有 2 条待确认")
    assert await window.permissions.menu.reply("web:owner", "2") is True
    with pytest.raises(PublicError):
        await asyncio.wait_for(first, 3)
    with pytest.raises(PublicError):
        await asyncio.wait_for(second, 3)


async def test_resolved_before_window_prints_no_menu(window):
    task = await pending(window, "danger_one")
    rows = await window.store.list("approval", scope=OWNER.session)
    await window.permissions.resolve(rows[0]["id"], True)
    await asyncio.wait_for(task, 3)
    await asyncio.sleep(0.15)
    assert await wait_text(window, "回数字") is None


async def test_menu_ignores_unrelated_text(window):
    task = await pending(window, "danger_one")
    await wait_text(window)
    try:
        assert await window.permissions.menu.reply("web:owner", "你好") is False
        assert await window.permissions.menu.reply("web:owner", "同意") is False
        assert await window.permissions.menu.reply("private:qq:1", "1") is False
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_new_request_queues_until_current_menu_closes(window):
    first = await pending(window, "danger_one")
    await wait_text(window, "需要确认：danger_one")
    second = await pending(window, "danger_two")
    assert await no_text(window, "需要确认：danger_two")   # 不并进已打开的菜单
    assert await window.permissions.menu.reply("web:owner", "1") is True
    text = await wait_text(window, "需要确认：danger_two")
    assert "2 拒绝" in text
    try:
        assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    finally:
        second.cancel()
        await asyncio.gather(first, second, return_exceptions=True)
```

`wait_text` 需要支持「等不到」——当 `needle` 找不到时它会等到超时抛错。给 `tests/test_approval_menu.py` 再加一个辅助：

```python
async def no_text(app, needle, wait=0.2):
    await asyncio.sleep(wait)
    return not any(needle in row["text"] for row in await app.store.list("outbox"))
```

并把 `test_resolved_before_window_prints_no_menu` 最后一行改成 `assert await no_text(window, "回数字")`。

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q`
Expected: 新增 4 个用例 FAIL（`reply` 不存在 / 文案不符）。

- [ ] **Step 3: 实现 list 阶段**

- `_list_text(requests)`：

```
有 {n} 条待确认：
{序号}. {tool.risk 中文}（{tool}）— {reason 的「要做」后内容，截 30 字}
...
回数字：
1 全部同意
2 全部拒绝
3 看详情
```

排序：`sorted(requests, key=lambda r: (RISK_ORDER[r.tool.risk], r.tool.name, r.key))`，编号从 1 开始。

- `_option(text) -> int | None`：`re.fullmatch(r"[1-9]\d?", text.strip())`。
- `_groups(requests)`：按 `(risk, tool)` 分组，返回按 `(RISK_ORDER, tool)` 排序的 `[(risk, tool, [req...])]`。
- `accepts(session, text)`：`session == self.target and self.batch is not None and self._valid(self._option(text))`；`_valid(n)` 按阶段：list 为 `1 <= n <= 3`；detail 为 `1 <= n <= 2*组数+1`；single 为 `1 <= n <= 4`。
- `reply(session, text) -> bool`：不满足 `accepts` 返回 `False`；否则先 `remaining = await self._sync()`，按阶段执行：
  - list 1 → 对 `remaining` 逐条 `await self._resolve(req, True)`，`await self._send(f"已全部同意：{len(remaining)} 条")`，`await self._finish()`，返回 `True`。
  - list 2 → 同上，`False`，文案 `已全部拒绝：{n} 条`。
  - list 3 → 转到 detail（Task 3；本任务先 `return False` 也行，但测试里不覆盖，保持占位注释）。
- `_sync() -> list[Request]`：对 `self.batch.requests` 中未 `settled` 的逐条读 `store.get("approval", key)`，状态不是 `pending` 或已过期就加进 `settled`；返回仍待处理的列表。
- `settled(key)`：把 `key` 记进当前批次的 `settled`，并 `self._spawn(self._refresh())`，`_refresh` 里若 `remaining` 为空则 `_finish()`。
- `_resolve(req, allow)`：`try: await self.permissions.resolve(req.key, allow) except PublicError: ...`；成功后 `self.batch.settled.add(req.key)`。
- `_finish()`：`batch, self.batch = self.batch, None`；无排队就返回；否则取 `self.queue[:PAGE_SIZE]`，剩余留在队列，`await self._open(entries)`。
- `_open` 里批次条数大于 1 时改用 `_list_text`。

- [ ] **Step 4: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py tests/test_permissions.py -q`
Expected: 全过。

---

### Task 3: 详情分组与逐条处理

**Files:**
- Modify: `src/shiritaibot/auth/menu.py`
- Test: `tests/test_approval_menu.py`

**Interfaces:**
- Consumes: Task 2 的 `_groups`、`accepts`、`reply`、`_sync`、`_resolve`、`_finish`。
- Produces: `_show_detail(remaining)`、`_show_single(remaining)`、`Batch.stage` 在 `"list"/"detail"/"single"` 间切换；`_detail_text(groups)`。

- [ ] **Step 1: 写失败测试**

```python
async def test_detail_groups_by_risk_then_tool(window):
    first = await pending(window, "danger_one", risk="dangerous")
    second = await pending(window, "qq_send", risk="normal")
    third = await pending(window, "shell_like", risk="sensitive")
    text = await wait_text(window, "有 3 条待确认")
    assert await window.permissions.menu.reply("web:owner", "3") is True
    detail = await wait_text(window, "详情，按类型分")
    assert detail.index("危险 · danger_one") < detail.index("敏感 · shell_like") < detail.index("一般 · qq_send")
    for task in (first, second, third):
        task.cancel()
    await asyncio.gather(first, second, third, return_exceptions=True)


async def test_detail_approves_only_one_group(window):
    first = await pending(window, "danger_one", risk="dangerous")
    second = await pending(window, "danger_one", risk="dangerous")
    third = await pending(window, "qq_send", risk="normal")
    await wait_text(window, "有 3 条待确认")
    await window.permissions.menu.reply("web:owner", "3")
    detail = await wait_text(window, "详情，按类型分")
    assert "1 danger_one 全部同意" in detail
    assert "3 qq_send 全部同意" in detail
    assert "5 逐条处理" in detail
    assert await window.permissions.menu.reply("web:owner", "1") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    assert json.loads(await asyncio.wait_for(second, 3))["ok"] is True
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(third, 0.2)
    third.cancel()
    await asyncio.gather(third, return_exceptions=True)


async def test_step_by_step_walks_single_menus(window):
    first = await pending(window, "danger_one")
    second = await pending(window, "danger_two")
    await wait_text(window, "有 2 条待确认")
    await window.permissions.menu.reply("web:owner", "3")
    await window.permissions.menu.reply("web:owner", "5")
    assert await window.permissions.menu.reply("web:owner", "1") is True   # 第一条同意
    assert await window.permissions.menu.reply("web:owner", "2") is True   # 第二条拒绝
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    with pytest.raises(PublicError):
        await asyncio.wait_for(second, 3)


async def test_expired_request_releases_queued_batch(window):
    first = await pending(window, "danger_one")
    await wait_text(window, "需要确认：danger_one")
    second = await pending(window, "danger_two")
    rows = await window.store.list("approval", scope=OWNER.session)
    row = next(r for r in rows if r["tool"] == "danger_one")
    row["status"] = "expired"
    await window.store.put("approval", row["id"], row, OWNER.session)
    await window.permissions.menu._refresh()
    text = await wait_text(window, "需要确认：danger_two")
    assert "danger_two" in text
    second.cancel()
    await asyncio.gather(first, second, return_exceptions=True)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q`
Expected: 3 个新用例 FAIL。

- [ ] **Step 3: 实现 detail / single 阶段**

- `_detail_text(groups)`：

```
详情，按类型分：
[{中文风险} · {tool}] {n} 条
...
回数字：
{2i+1} {tool} 全部同意
{2i+2} {tool} 全部拒绝
...
{2n+1} 逐条处理
```

- list 3 → 若 `len(groups) == 1` 直接进 single（`stage="single"`，`_show_single(remaining)`），否则 `stage="detail"`、`_show_detail(remaining)`。回复文案 `已打开详情。`
- detail 阶段：`n == 2i+1` → 组内逐条 `_resolve(req, True)`，`await self._send(f"{tool}：已全部同意 {len(group)} 条")`，`await self._refresh()`；`n == 2i+2` 同理拒绝；`n == 2*G+1` → `stage="single"`，`await self._show_single(remaining)`。
- `_show_single(remaining)`：取第一条未结算的，`await self._await_reason(req)` 后发单条菜单。
- single 阶段回复：1 → `_resolve(req, True)`，文案 `已同意：{tool} {key}`；2 → 拒绝，`已拒绝：{tool} {key}`；3、4 在 Task 4/5 实现，本任务先 `return False` 前不要吞掉，保持 `_valid` 允许 3/4 但分支写 `raise NotImplementedError`？不行。**本任务把 3/4 当作 1/2 处理会误导**——所以在 Task 4/5 之前，`_can_grant` 返回 `False`，`_valid` 对 single 阶段只允许 1/2，文案也不列 3/4。Task 4 再放开。
- `_refresh()`：`remaining = await self._sync()`；空 → `_finish()`；`stage == "single"` → `_show_single(remaining)`；`stage == "detail"` → 组数变了就 `_show_detail(remaining)`。
- `_resolve` 后调 `await self._refresh()`（single 阶段推进到下一条；detail 阶段重画剩余分组）。

- [ ] **Step 4: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q`
Expected: 全过。（Task 4 会把 3/4 加回来，届时本任务的用例不受影响。）

---

### Task 4: 五分钟免问（单条选项 3）

**Files:**
- Modify: `src/shiritaibot/auth/__init__.py`（`quick` 表、`quick_granted`、`grant_quick`）
- Modify: `src/shiritaibot/auth/menu.py`（`_can_grant`、single 文案与 3 号分支）
- Test: `tests/test_approval_menu.py`、`tests/test_permissions.py`

**Interfaces:**
- Consumes: Task 1 的 `SENSITIVE_FILE`、`Request`。
- Produces:
  - `Permissions.quick: dict[tuple[str, str], float]`
  - `Permissions.quick_granted(self, ctx: Context, tool: Tool, args: dict) -> bool`
  - `Permissions.grant_quick(self, session: str, tool: str, args: dict) -> None`
  - `ApprovalMenu._can_grant(self, req: Request) -> bool`

- [ ] **Step 1: 写失败测试**

```python
async def test_single_option_three_grants_for_five_minutes(window):
    first = await pending(window, "danger_one")
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "3") is True
    assert json.loads(await asyncio.wait_for(first, 3))["ok"] is True
    later = await pending(window, "danger_one")
    assert json.loads(await asyncio.wait_for(later, 3))["ok"] is True
    rows = await window.store.list("approval", scope=OWNER.session)
    assert all(row["status"] != "pending" for row in rows)


async def test_quick_grant_does_not_cross_sessions(window):
    first = await pending(window, "danger_one")
    await wait_text(window)
    await window.permissions.menu.reply("web:owner", "3")
    await asyncio.wait_for(first, 3)
    other = Context("private:qq:2", "owner", "agent", True)
    task = await pending(window, "danger_one", ctx=other)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(task, 0.2)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_sensitive_shell_has_no_grant_options(window):
    task = await pending(
        window, "shell", risk="dangerous", args={"command": "cat config.toml"}
    )
    text = await wait_text(window)
    assert "3 五分钟内" not in text
    assert "4 以后" not in text
    assert "只提供 1/2" in text
    assert await window.permissions.menu.reply("web:owner", "3") is False
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q -k "grant or sensitive_shell"`
Expected: FAIL。

- [ ] **Step 3: 实现**

`Permissions`：
- `__init__` 加 `self.quick: dict[tuple[str, str], float] = {}`。
- `quick_granted(ctx, tool, args)`：`ctx.background` 直接 `False`；`tool.name == "shell"` 且 `SENSITIVE_FILE.search(str(args.get("command", "")))` 直接 `False`；`(ctx.session, tool.name)` 不在表或已过期（`<= time.monotonic()`）就清掉并 `False`，否则 `True`。
- `grant_quick(session, tool, args)`：写 `self.quick[(session, tool)] = time.monotonic() + QUICK_SECONDS`（从 `auth.menu` 导入），然后扫 `store.list("approval", scope=session)`，对 `status == "pending"`、`tool == tool`、且不是敏感 shell 的逐条 `await self.resolve(row["id"], True)`，`PublicError` 忽略。
- `check()` 在 `decision == "approve"` 之后、建 key 之前：

```python
        if decision == "approve" and self.quick_granted(ctx, tool, args):
            log.log(f"五分钟免问生效，直接放行 {tool.name}", describe_session(ctx.session))
            return
```

`ApprovalMenu`：
- `_can_grant(req)`：`not req.ctx.background` 且不是敏感 shell。
- 单条文案：`_can_grant` 为真才列 3/4，否则列 `（本次命令涉及密钥文件或来自后台会话，只提供 1/2）`。
- `_valid` single 阶段：`_can_grant` 真则 `1..4`，否则 `1..2`。
- single 回复 3：`await self.permissions.grant_quick(req.ctx.session, req.tool.name, req.args)`；`await self._resolve(req, True)`；文案 `五分钟内 {tool} 的审批将自动同意`。

- [ ] **Step 4: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py tests/test_permissions.py -q`
Expected: 全过。

---

### Task 5: 永久免问（单条选项 4）

**Files:**
- Modify: `src/shiritaibot/auth/__init__.py`（`grant_permanent`、`grant_rows`、`revoke_grant`、`remember`）
- Modify: `src/shiritaibot/auth/menu.py`（4 号分支）
- Modify: `src/shiritaibot/app.py`（`self.permissions.memory = self.memory`）
- Test: `tests/test_approval_menu.py`

**Interfaces:**
- Consumes: `Permissions.set_session(ctx, SessionPolicy)`、`Permissions.session_policy(ctx)`、`store.append(session, turn, message)`、`MemoryManager.save_fact(ctx, content, evidence, subject="")`。
- Produces:
  - `Permissions.grant_permanent(self, req_session: str, tool: str, risk: str, reason: str) -> str`
  - `Permissions.grant_rows(self, session: str) -> list[dict]`
  - `Permissions.revoke_grant(self, ctx: Context, tool: str) -> str`
  - `approval_grant` 记录：`store.put("approval_grant", f"{session}|{tool}", {"tool", "risk", "reason", "created"}, session)`

- [ ] **Step 1: 写失败测试**

```python
async def test_permanent_grant_updates_policy_record_and_memory(window):
    task = await pending(window, "danger_one", risk="dangerous")
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "4") is True
    assert json.loads(await asyncio.wait_for(task, 3))["ok"] is True

    profile = window.config.value.policy.sessions[OWNER.session]
    assert profile.tools["danger_one"] == "allow"
    rows = await window.store.list("approval_grant", scope=OWNER.session)
    assert rows[0]["tool"] == "danger_one"

    facts = await window.store.query("SELECT content FROM mem_fact")
    assert any("danger_one" in row["content"] for row in facts)

    later = await pending(window, "danger_one")
    assert json.loads(await asyncio.wait_for(later, 3))["ok"] is True


async def test_sensitive_shell_cannot_get_permanent_grant(window):
    task = await pending(
        window, "shell", risk="dangerous", args={"command": "cat master.key"}
    )
    await wait_text(window)
    assert await window.permissions.menu.reply("web:owner", "4") is False
    assert "shell" not in window.config.value.policy.sessions.get(OWNER.session, window.config.value.policy.owner_default).tools
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_background_approval_only_offers_agree_and_deny(window):
    ctx = Context("background:job", "owner", "agent", True, background=True)
    task = await pending(window, "danger_one", ctx=ctx)
    text = await wait_text(window)
    assert "1 同意" in text and "2 拒绝" in text
    assert "3 五分钟内" not in text and "4 以后" not in text
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q -k "permanent or background"`
Expected: FAIL。

- [ ] **Step 3: 实现**

`Permissions.__init__` 加 `self.memory = None`；`app.py` 在 `self.memory = MemoryManager(...)` 之后加 `self.permissions.memory = self.memory`。

`grant_permanent(session, tool, risk, reason)`：
1. `ctx = Context(session, "owner", "agent", owner=True)`。
2. `profile = self.session_policy(ctx).model_copy(deep=True)`；`profile.tools[tool] = "allow"`；`await self.set_session(ctx, profile)`。
3. `await self.store.put("approval_grant", f"{session}|{tool}", {"tool": tool, "risk": risk, "reason": reason, "created": now().timestamp()}, session)`。
4. 记忆（尽力而为）：`note = f"审批授权：{tool} 以后都同意"`；`turn = uid()`；`await self.store.append(session, turn, {"role": "tool", "content": note})`；`ctx = Context(session, "owner", "agent", owner=True, turn=turn)`；`await self.memory.save_fact(ctx, f"主人在会话 {session} 批准了 {tool} 的长期免问（{now():%Y-%m-%d}）", note)`，整体 `try/except Exception` 记 `log.warning`，不影响授权。
5. 扫同会话同工具待审批，逐条 `resolve(..., True)`。
6. 返回 `以后 {tool} 都同意，已记入长期记忆`。

`menu` single 回复 4：`await self.permissions.grant_permanent(...)` 后 `_resolve(req, True)`，把返回文案发出。

文案与校验：`_can_grant(req)` 为假时 `_valid` 不允许 4，`reply("4")` 返回 `False`。

`grant_rows(session)`：`await self.store.list("approval_grant", scope=session)`。

`revoke_grant(ctx, tool)`（Task 6 用，本任务先实现）：
1. `row = await self.store.get("approval_grant", f"{ctx.session}|{tool}")`，没有则 `raise PublicError("当前会话没有这个工具的免问授权。")`。
2. `profile = self.session_policy(ctx).model_copy(deep=True)`；若 `profile.tools.get(tool) == "allow"` 改成 `"manual"` 并 `set_session`。
3. `await self.store.delete("approval_grant", f"{ctx.session}|{tool}")`；`self.quick.pop((ctx.session, tool), None)`。
4. 记忆补充一条「已撤销」（同上 note 机制）。
5. 返回 `已撤销 {tool} 的免问授权，之后需要人工确认。`

- [ ] **Step 4: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py tests/test_permissions.py -q`
Expected: 全过。

---

### Task 6: `/grants` 查询与撤销

**Files:**
- Modify: `src/shiritaibot/agent/commands.py`
- Test: `tests/test_approval_menu.py`

**Interfaces:**
- Consumes: `Permissions.grant_rows(session)`、`Permissions.revoke_grant(ctx, tool)`。
- Produces: `COMMANDS["grants"]`、命令分支 `name == "grants"`。

- [ ] **Step 1: 写失败测试**

```python
async def test_grants_command_lists_and_revokes(window):
    from shiritaibot.agent.commands import command

    task = await pending(window, "danger_one", risk="dangerous")
    await wait_text(window)
    await window.permissions.menu.reply("web:owner", "4")
    await asyncio.wait_for(task, 3)

    ctx = Context("private:qq:1", "owner", "agent", True)
    await command(window.runtime, ctx, "/grants")
    listing = await wait_text(window, "免问授权")
    assert "danger_one" in listing

    await command(window.runtime, ctx, "/grants 撤销 danger_one")
    assert await no_text(window, "已撤销 danger_one")

    await command(window.runtime, ctx, "/grants")
    assert await wait_text(window, "没有免问授权")


async def test_grants_command_is_owner_private_only():
    from shiritaibot.agent.commands import COMMANDS

    assert "grants" in COMMANDS
```

**注意：** 测试里授权写在 `OWNER.session`（`private:qq:1`），命令上下文也是 `private:qq:1`，两者一致；`window.app` 的 `deliver` 会往 outbox 写，`wait_text` 直接读 outbox 即可。

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q -k grants`
Expected: FAIL（未知命令）。

- [ ] **Step 3: 实现**

`COMMANDS` 加：

```python
"grants": "查看免问授权；/grants 撤销 工具名（仅主人私聊）",
```

`command()` 里把 `"grants"` 加进私有命令集合（`{"tasks", "memory", "approve", "deny", "grants"}`）和 `/help` 的过滤集合。新分支：

```python
elif name == "grants":
    tool = argument.removeprefix("撤销").strip()
    if argument.startswith("撤销"):
        if not tool:
            raise PublicError("用法：/grants 撤销 工具名")
        answer = await runtime.registry.permissions.revoke_grant(ctx, tool)
    else:
        rows = await runtime.registry.permissions.grant_rows(ctx.session)
        answer = "当前会话没有免问授权。" if not rows else (
            f"免问授权（{ctx.session}）：\n"
            + "\n".join(
                f"{row['tool']} · {RISK_LABELS.get(row.get('risk'), row.get('risk', ''))} · "
                f"{datetime.fromtimestamp(row['created']):%Y-%m-%d} — {row.get('reason', '')}"
                for row in rows
            )
            + "\n撤销：/grants 撤销 工具名"
        )
```

`RISK_LABELS` 从 `shiritaibot.auth.menu` 导入；时间用 `from datetime import datetime`（文件顶部加）。

- [ ] **Step 4: 运行测试**

Run: `.venv\Scripts\python -m pytest tests/test_approval_menu.py -q -k grants`
Expected: PASS。

---

### Task 7: QQ 数字回复接入与应用装配

**Files:**
- Modify: `src/shiritaibot/adapters/onebot11.py:369-418`
- Modify: `tests/test_permissions.py`（数字回复走 adapter 的集成测试）
- Test: `tests/test_permissions.py`

**Interfaces:**
- Consumes: `ApprovalMenu.accepts(session, text)`、`ApprovalMenu.reply(session, text) -> bool`。
- Produces: 主人私聊里的合法数字直接处理并 `return`；非法数字照常进入对话。

- [ ] **Step 1: 写失败测试**

```python
async def test_chat_number_menu_reply_routes_to_menu(app):
    app.permissions.menu.target = "private:qq:1"
    effects = []

    async def handler(ctx, args):
        effects.append("ran")
        return {"ok": True}

    app.registry.add(Tool("danger_one", "", schema({}), handler, risk="dangerous"))
    task = asyncio.create_task(
        app.registry.execute(Context("private:qq:1", "owner", "agent", True), "danger_one", {})
    )
    rows = await eventually(lambda: app.store.list("approval"))

    async def menu_ready():
        texts = [row["text"] for row in await app.store.list("outbox")]
        return any("1 同意" in text for text in texts)

    await eventually(menu_ready)
    try:
        await chat_reply(app, "1")
        assert effects == ["ran"]
        assert json.loads(await asyncio.wait_for(task, 3))["ok"] is True
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
```

为让测试不必等 2 秒，在测试里把窗口改小：

```python
@pytest.fixture(autouse=True)
def fast_window(monkeypatch):
    import shiritaibot.auth.menu as menu

    monkeypatch.setattr(menu, "WINDOW", 0.5)
    monkeypatch.setattr(menu, "REASON_BUDGET", 0.05)
```

（加在 `tests/test_permissions.py` 顶部即可；`chat_reply` 已有。）

- [ ] **Step 2: 运行确认失败**

Run: `.venv\Scripts\python -m pytest tests/test_permissions.py -q -k number_menu`
Expected: FAIL（数字消息被当成普通对话，`effects` 为空）。

- [ ] **Step 3: 实现适配器接入**

在 `receive()` 中：

1. `control` 判定（第 376-378 行）加一项：

```python
        menu_reply = (
            not group
            and user == cfg.owner_id
            and self.permissions.menu.accepts(session, text)
        )
        control = (
            text.startswith("/")
            or text in {"停", "取消"}
            or approval_reply(text) is not None
            or menu_reply
        )
```

2. 唤醒词剥离（第 393 行）同时认菜单选项：

```python
                if (
                    rest.startswith("/")
                    or rest in {"停", "取消"}
                    or approval_reply(rest)
                    or self.permissions.menu.accepts(session, rest)
                ):
```

3. 在 `approval_reply` 分支之后、`hooks.dispatch` 之前：

```python
        if (
            not group
            and user == cfg.owner_id
            and await self.permissions.menu.reply(session, text)
        ):
            return
```

`reply` 自己发确认与下一屏文案，适配器不再 `enqueue`。

- [ ] **Step 4: 运行测试与全量回归**

Run: `.venv\Scripts\python -m pytest tests/test_permissions.py tests/test_approval_menu.py -q`
Expected: 全过。

Run: `.venv\Scripts\python -m pytest -q`
Expected: 全过（除已知的 `tests/test_github_plugin.py` 两个与本改动无关的 `KeyError: 'exit_code'`；若出现，报告时注明）。

---

## 实现说明（builder 需要记录的判断）

- **分组菜单选项数**：按「每组 2 个 + 逐条处理」编号，批次最多 5 条，所以最多 5 组、11 个选项；数字解析接受 1–99，手机输入两位数字可接受。没有做翻页，因为 5 条上限已保证一屏。
- **选项 3/4 作用到当前批次里同会话同工具的待审批**：`grant_quick` / `grant_permanent` 在写授权后立即把同会话同工具仍待处理的审批一并放行，符合「都同意」的字面意思。
- **超时只看不发**：看门狗只在全部条目失效时关闭批次并放出排队；单条超时不会主动重画菜单，主人下一次回复时 `_sync` 会剔除失效项。这样避免「菜单自己刷屏」。
- **记忆证据**：数字回复不是一个 turn，无法作为 `save_fact` 的 verbatim 证据；授权时另起一个 turn，写一条 `role: "tool"` 的授权记录（`审批授权：{tool} 以后都同意`），以它作为证据，权威级别为 `derived`，内容里写明是主人的选择。
- **参数行**：模型生成的说明可能省略细节，所以单条菜单始终附 `参数：…`（截 300 字），shell 也一样。
