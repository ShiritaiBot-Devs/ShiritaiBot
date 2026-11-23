# Official QQ bot

**English** · [简体中文](QQ-OFFICIAL.md)

Create a bot on the QQ Open Platform. In the WebUI, go to **Connections → Add → Official QQ bot**, enter the App ID and App Secret, then enable it and save.

Under **Event subscription and callbacks** on the Open Platform, enter the callback path shown on the Connections page, prefixed with your public HTTPS domain. For example, if the instance ID is `qq-official`, the URL is:

```text
https://bot.example.com/api/adapters/qq-official/qq-official/callback
```

Forward that path to ShiritaiBot with a reverse proxy, and add the domain to `--allowed-host` when you start it. Subscribe the app to `C2C_MESSAGE_CREATE` and `GROUP_AT_MESSAGE_CREATE`. If the platform opens ordinary group messages to the app, you can also subscribe to `GROUP_MESSAGE_CREATE`. If the platform asks for an API IP allow list, enter the outgoing IP of the machine running the instance.

The official QQ API uses separate OpenIDs for one-on-one users, groups, and group members. Add the groups and users you allow to the adapter's allow lists. To find the OpenID of a session that isn't configured yet, send a test message and switch the WebUI log to Debug. Only the owner's one-on-one chat gets owner permissions; group chats run with group session permissions.

The adapter supports text messages and passive replies in QQ one-on-one and group chats. Replies are tied to the received message ID and are subject to the platform's reply time and count limits. Attachments appear as placeholders in the incoming text. Only the webhook is supported for now; channels, media upload, and proactive push are planned separately.

See [Event subscription and notifications](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html), [Signature verification](https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/sign.html), and [Messaging overview](https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/overview.html) (in Chinese).
