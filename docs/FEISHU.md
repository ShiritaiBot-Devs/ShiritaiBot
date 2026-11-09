# 飞书应用机器人

飞书可作为 ShiritaiBot 的聊天入口。主人直接私聊机器人，指定群里的成员也可用句首关键词唤醒它。运行 ShiritaiBot 的机器需要能访问飞书开放平台，长连接由 ShiritaiBot 主动建立。

## 创建应用和绑定主人

1. 在飞书开放平台创建**企业自建应用**，添加机器人能力，复制 App ID 和 App Secret。
2. 在 ShiritaiBot 管理台的「协议连接」添加飞书机器人，填入应用凭证并保存。首版每个 ShiritaiBot 实例启用一个飞书长连接，可和 QQ 同时运行。
3. 在飞书开发者后台的「事件与回调」选择长连接，并订阅 `im.message.receive_v1`。在权限管理中开通 `im:message.p2p_msg:readonly` 和 `im:message:send_as_bot`。发布应用版本，将需要使用的账号纳入可用范围。
4. 用主人账号给机器人发一条测试私聊。管理台的连接提示会显示最近一条私聊的发送者 `open_id`。核对发送账号后将它填入「主人 Open ID」并保存，再发一条消息验证回复。每个应用的 `open_id` 独立，按当前应用收到的值填写。

飞书可能要求长连接先上线，才能保存事件订阅方式。配置示意见 [飞书长连接文档](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-)。

## 群唤醒

为应用申请并发布 `im:message.group_msg`，把机器人加入测试群，在管理台填写群的 `oc_…` ID。唤醒词默认是 `小知`。在该群发 `小知，帮我整理一下`，机器人应直接回复。再发一条不以唤醒词开头的消息，确认没有回复。可用飞书开放平台的 [群组 API](https://open.feishu.cn/document/server-docs/im-v1/chat/list) 查询应用所在群的 `chat_id`。

当诊断显示群消息权限缺失或尚未确认时，检查权限审批、应用发布和事件订阅，再用不带 `@` 的消息实测。仅开通群 `@` 事件时，飞书不会推送普通群消息。[飞书消息 FAQ](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/guide/faq)

开启「群聊上下文」后，机器人被唤醒时会参考本群的近期消息。主人在群里也使用群聊权限，私聊记忆只在主人私聊的 Agent 模式中可用。

## 选择记忆

「飞书记忆」默认独立保存。选「默认记忆」时，主人飞书私聊与 QQ、WebUI 共享原有记忆。切换后从选中的库对应的上下文继续，旧对话仍可查阅。QQ 和 WebUI 继续使用默认库。主动通知和工具审批沿用原有目标，未配置 QQ 目标时在 WebUI 查看。
