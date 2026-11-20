---
name: shiritai-plugin-dev
description: 开发 ShiritaiBot 插件（API v2）：写 /指令、消息监听、LLM 工具、Agent 与发送 Hook、主动事件源、配置表单，以及插件调试与分发。主人让写/改/排查 ShiritaiBot 插件、问插件 API 用法、或要把旧版（plugin.toml / Entry）插件迁移到新版时使用。
license: AGPL-3.0-or-later
---

# ShiritaiBot 插件开发

ShiritaiBot 是事件驱动的个人 agent 框架，插件与它在同一进程运行（可信代码、无沙箱）。
插件 API 参考 AstrBot，当前为 API v2：一切从 `shiritaibot.api` 导入；框架其他模块是内部实现，不要直接导入。

## 按任务先读参考，再动笔

references/ 目录存放官方文档（源：shiritaibot.dev/docs/plugins），按需读取，不要凭记忆写 API：

| 任务 | 先读 |
| --- | --- |
| 从零写插件、目录结构、metadata.yaml、加载调试、分发 | getting-started.md |
| /指令、指令组、参数注入、消息监听、MessageEvent 字段 | messages.md |
| 给模型提供工具（@hook.llm_tool、risk 分级、ToolContext） | tools.md |
| _conf_schema.json 配置声明、secret 密钥处理 | config.md |
| 修改模型请求/回复、拦截发送、各生命周期 Hook | hooks.md |
| 把外部事件送进主动性引擎（ExternalEvent、轮询事件源） | proactive.md |
| self.context 全量 API（发消息/调用模型/KV/协议端/shell 与子代理注入） | context.md |

## 最小骨架

直接复制 assets/hello_world/（metadata.yaml + main.py + _conf_schema.json）改。速记：

- 目录名就是插件 ID（`[a-z0-9_]`，推荐 `srtb_plugin_<名字>`）；一个 main.py 只放一个 `Plugin` 子类。
- 生命周期：`initialize()` 里读配置、启动任务；`terminate()` 里取消它们。重载 = 旧实例 terminate → 新实例 initialize。
- 指令：`@hook.command("名", aliases={"别名"})`，docstring 首行进 /help；`yield event.plain_result(...)` 回复；预期内错误 `raise PublicError("提示")`。
- 工具：`@hook.llm_tool("工具名", risk="safe", chat=True)`，参数用 docstring 的 `Args:`（`参数名(类型): 说明`）；工具名全局唯一，建议带插件名前缀。
- 配置：`_conf_schema.json` 声明字段，`initialize()` 里读 `self.config["键"]`；用户保存配置会自动重载插件。

## 硬约束（违反会导致加载失败或行为异常）

1. `@hook.llm_tool` 的 `risk` 必填：safe（只读）/ normal（小写入可恢复）/ sensitive（对外影响或难撤回）/ dangerous。有副作用不得标 safe；拿不准就标 sensitive。
2. 指令不能与内置指令（/help、/status、/stop 等）或其他插件指令重名；工具名不能重复。
3. 超时上限：Hook 10 秒、消息监听器 30 秒、主动事件 Hook 30 秒、工具默认 60 秒（`timeout=` 可调）、指令 10 分钟。超长工作放 `asyncio.create_task`。
4. 严禁阻塞事件循环：禁用 time.sleep 和同步 HTTP；同步调用用 `asyncio.to_thread()` 包；网络用 `httpx.AsyncClient`。
5. initialize() 启动的任务/连接必须在 terminate() 里关掉；轮询循环体必须 try/except（一次异常就会让后台任务悄悄退出）。
6. secret 配置字段加密存储，代码里拿到的是 `secret:...` 引用，用 `await self.context.resolve_secret(...)` 换明文；明文不打日志、不返回给模型、不进子进程环境变量。
7. 往主动性引擎提交的事件 payload 只放必要内容：邮件、网页等外部内容可能包含针对模型的指令注入。

## 调试与部署要点

- 插件部署目录是 `data/plugins/<id>/`；主仓库 `src/shiritaibot/plugin/` 下是随仓库发布的 v2 宿主插件。两个搜索路径同名时 data/plugins 优先。
- 改代码 → WebUI「插件」页点「重载当前插件」即生效，不必重启 bot；加载失败时旧实例继续运行，错误显示在插件列表。
- 日志：`self.logger.log/warning/error(..., exc=exc)` → 终端 + data/logs/shiritaibot.log + WebUI 日志页；长内容用 `brief(value, limit)` 压行。
- 依赖声明写进插件目录的 requirements.txt 或 pyproject.toml 的 [project] dependencies，0.3+ 运行时会汇总解析并自动装进独立运行时环境；手动 pip 装的未声明包会在下次精确同步时被清掉。
- 语法快检用运行时 venv 的 python：`python -m py_compile main.py`；运行时 venv 在 `data/runtime/projects/<代次>/.venv`，不是仓库根的 .venv。
- 部署环境带全局代理变量时，请求国内 API 建议 `httpx.AsyncClient(trust_env=False, headers={浏览器 UA})`：国内站走代理容易被对端风控断连；还要注意限频，轮询失败要指数退避（踩坑案例：东方财富行情接口按 TLS 指纹掐程序化请求）。
- KV 存小 JSON 数据（self.get_kv_data / put_kv_data / delete_kv_data）；文件放大文件放 `self.data_dir`（= data/plugin_data/<id>/，卸载插件不删除）；用户要看到/改的设置放 _conf_schema，不要塞 KV。

## 分发

GitHub 公开仓库（WebUI「安装插件」填地址，下载默认分支）或 ZIP（可多套一层目录）；必须包含 metadata.yaml 与 main.py；安装后默认停用，需填配置再启用。
