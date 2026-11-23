# QQ 官方机器人

[English](QQ-OFFICIAL.en.md) · **简体中文**

在 QQ 开放平台创建机器人。在 WebUI **适配器 → 添加机器人 → QQ 官方机器人**填写 App ID 和 App Secret，然后启用并保存。

在开放平台的**事件订阅与回调**中填入适配器页面显示的回调路径，前面加上你的公网 HTTPS 域名。例如实例标识为 `qq-official` 时，地址为：

```text
https://bot.example.com/api/adapters/qq-official/qq-official/callback
```

用反向代理把该路径转发至 ShiritaiBot，启动时将域名加入 `--allowed-host`。给应用订阅 `C2C_MESSAGE_CREATE` 和 `GROUP_AT_MESSAGE_CREATE`。如果平台为该应用开放普通群消息，可以同时订阅 `GROUP_MESSAGE_CREATE`。如平台要求配置接口 IP 白名单，填运行实例的出口 IP。

QQ 官方 API 使用单聊用户、群和群成员各自的 OpenID。把允许的群和用户加入适配器白名单。可先发送一条测试消息，在 WebUI 日志切到 Debug 查看未配置会话的 OpenID。只有主人单聊走主人权限，群聊按群会话权限执行。

适配器支持 QQ 单聊和群聊的文本消息与被动回复。回复绑定收到的消息 ID，受平台回复时限和次数约束。附件在入站文本中显示为占位符。当前接入范围为 Webhook，频道收发、媒体上传和主动推送另行接入。

参考 [事件订阅与通知](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html)、[签名校验](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html)和[消息收发概述](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html)。
