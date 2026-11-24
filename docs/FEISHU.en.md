# Feishu / Lark app bot

**English** · [简体中文](FEISHU.md)

Feishu (Lark outside China) can be a chat entry point to ShiritaiBot. The owner chats with the bot privately, and members of chosen groups can wake it with a wake word at the start of a message. The machine running ShiritaiBot needs to reach the Feishu Open Platform; ShiritaiBot opens the long connection itself.

## Create by scanning a QR code

In the console, open **Connections**, click **Add**, choose **Feishu app bot**, then click **Create by scanning a QR code**. Choose Feishu or Lark, generate the QR code, and confirm on your phone. Back in the console, choose a memory store and click **Save and connect**. If a Feishu connection already exists, you can save the new instance as disabled first.

If your organization requires publishing or approval, handle that on the Feishu Open Platform. Once the connection works, bind the owner as described below.

If creation succeeded on the phone but the console shows no instance, connect the app with **Configure manually** below.

## Configure manually

1. On the Feishu Open Platform, open the app to connect and copy the App ID and App Secret from **Credentials & Basic Info**. If you don't have an app yet, create a custom enterprise app and add the bot capability.
2. In the console, under **Connections**, click **Add**, choose **Feishu app bot**, then click **Configure manually**. Choose the platform, enter the App ID and App Secret, turn on **Enable connection**, and save. Each ShiritaiBot instance can run one Feishu long connection alongside QQ.
3. In the developer console under **Events & Callbacks**, choose the long connection and subscribe to `im.message.receive_v1`. Under permissions, grant `im:message.p2p_msg:readonly` and `im:message:send_as_bot`. Publish an app version and add the accounts that will use it to its availability scope.

Feishu may require the long connection to be online before it lets you save the event subscription method. See the [Feishu long connection docs](https://open.feishu.cn/document/ukTMukTMukTM/uYDNxYjL2QTM24iN0EjN/event-subscription-configure-/request-url-configuration-case) for an example.

## Bind the owner

Send the bot a test private message from the owner's account. The connection status in the console shows the `open_id` of the latest private message's sender. Check that it's your account, enter it as **Owner Open ID**, save, and send another message to confirm the bot replies. `open_id` differs per app, so use the value this app received.

## Tool permissions

In the console, open **Capabilities & Permissions → Tools**, set **Scope** to **A specific Main session**, and select the owner's Feishu private chat. Adjust individual tool permissions as needed, then click **Save and apply**. For example, set `feishu_file_send` to **No approval needed**, and the next attachment sent in that private chat uses the saved policy.

When manual approval is needed, reply in that Feishu private chat as the message instructs, or handle it in the console. After switching to another Feishu app or owner account, configure the new session's permissions separately. Feishu group chats use Chat mode.

## Send attachments

In the owner's private chat, you can ask the bot to send back a file it created in the session's working directory as a Feishu attachment, for example "send me the `calculator.c` you just wrote as an attachment". Existing apps need **Read and upload images or files** (`im:resource`) granted on the Feishu Open Platform and a published version. Apps created by scanning request this permission automatically.

## Wake the bot in groups

Request and publish `im:message.group_msg` for the app, add the bot to a test group, and enter the group's `oc_…` ID in the console. For ordinary conversation, start the message with the adapter's wake word, `小知` by default (for example `小知, summarize this for me`); you can set your own. Built-in commands and registered plugin commands can be sent directly by default. After turning on **Group / commands need waking** under **General → Names and wake words**, call them with `@bot /command` or `小知, /command`. Use the Feishu Open Platform's [chat API](https://open.feishu.cn/document/server-docs/im-v1/chat/list) to look up the `chat_id` of groups the app is in.

If the diagnostics show the group message permission as missing or unconfirmed, check the permission approval, app publishing, and event subscription, then test with a message that doesn't @ the bot. If only group @ events are enabled, Feishu doesn't push ordinary group messages. See the [Feishu messaging FAQ](https://open.feishu.cn/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/guide/faq).

With **Group chat awareness** on, the bot takes the group's recent messages into account when woken. In groups, the owner also has group permissions; private-chat memory is only available in Agent mode in the owner's private chat.

## Choose a memory store

**Separate Feishu memory** is kept apart by default. With **Default memory**, the owner's Feishu private chat shares the existing memory with QQ and the WebUI. After switching, the conversation continues from the chosen store's context, and old conversations remain readable. QQ and the WebUI keep using the default store. Proactive notifications still go to the QQ owner private chat or the WebUI set in the configuration.
