# Sticker manager

**English** · [简体中文](MEMES.md)

The sticker manager lets the bot send stickers in QQ chats. The code is in `src/shiritaibot/meme/` and has three parts, plus three model tools and the WebUI's **Stickers** page. Configuration lives in `[meme]` in `config.toml`; the models it uses are set in `routes.meme`, next to the other routes.

```
Group stickers ──► Collector (hooker) ──► Data core (core) ◄── meme_search / meme_view / meme_save
                                              │                        ▲
                                              ▼                        │
Model call ◄── Injector: prompt ───────────────────────────────────────┘
Model reply ──► Injector: split ──► outbox ──► OneBot (text + sticker + text)
```

## 1. Data core (`meme/core.py`)

- Images are stored as `<home>/memes/<id>`, where `id` is the first 16 hex digits of the image's SHA-256, so identical images are stored once.
- Metadata is a kind=`meme` record in the `records` table: `category` (category tag), `description` (short text description), `mime`, `bytes`, `source` (`group` collected from a group, `agent` saved by the model, `manual` uploaded in the WebUI), `origin`, and `created`.
- Categories are set in `meme.categories`, with 10 by default: happy, sad, angry, think, surprised, shy, love, tired, smug, greet. Names may only use lowercase letters, digits, and underscores. Each category's note is shown both to the main model and to the category judgment.
- Descriptions are written by the multimodal text model in the `meme` route (`CAPTION_PROMPT`, at most 80 characters). GIFs and animated WebPs are turned into a PNG of their first frame before going to the model, because not every API accepts animated images; the original is still what gets sent.
- Description vectors are stored in the `meme_vector` table (separate from memory's `mem_vector`) and kept in sync by a background loop using the Embedding route. Without an Embedding model, search falls back to keyword matching.

## 2. Collector (`meme/hooker.py`)

Each time someone else posts a message with a sticker in an allowed group, the collector tries to collect one with probability

    p = collect_rate × (1 − stored / capacity)

so it slows down as the library fills and stops when it's full. Only one sticker is collected at a time, and message handling isn't blocked.

A "sticker" is an `image` segment reported by NapCat with `sub_type≠0` (or a `summary` containing the word for sticker), or an `mface` segment. With `stickers_only` turned off, ordinary images count too. Images are only downloaded from QQ's own HTTPS CDN, up to 2 MB.

Collection steps:

1. Describe: the `meme` route's multimodal text model writes a description. The decision model can't see images, so the description has to come before the judgment.
2. Judge: the default persona's prompt and the description go to the `meme` route's decision model (Jev), which answers two questions at once: `fit` (`noul`: would this persona send this sticker?) and `category` (`choice`: which category does it belong to?). If there's no decision model or the answer is invalid, the same route's text model answers in JSON instead.
3. Stickers with `fit` below `fit_threshold` aren't collected, and this is remembered in memory so they aren't judged again.

## 3. Injector (`meme/inject.py`)

**Prompt**: in QQ private and group chats (not the WebUI or the Proactive Loop), a `<memes>` section is added to the end of the system prompt. It explains the tag format, lists all configured categories, and gives the per-reply limit. It depends only on the configuration, not on the library's contents, so collecting new stickers doesn't change it and the prompt cache keeps hitting.

**Splitting**: this happens after the send function `AdapterHub.enqueue` and before the protocol client. In a reply,

- `<meme>happy</meme>` sends a random sticker from that category;
- `<meme>0123456789abcdef</meme>` (or a unique prefix of at least 6 characters) sends that specific sticker;

each becomes a separate outbox message. When a tag is in the middle of a sentence, the reply is split into three messages sent in order: text, sticker, text. Tags for unknown categories, empty categories, and anything beyond `max_per_reply` are removed, and the text around them is merged into one message. Stickers are sent as `base64://` image segments (`sub_type=1`); in group chat context and the WebUI they show as `[Sticker: description]`.

## 4. Tools (`meme/tools.py`)

| Tool | Risk | Purpose |
| --- | --- | --- |
| `meme_search` | safe | Semantic search by description (Embedding, topped up with keywords), optionally within a category |
| `meme_view` | safe | View a sticker image |
| `meme_save` | normal | Save an image received in the current session (`[image ref=...]`) to the sticker library, subject to capacity |

All three tools are also available in Chat mode and are given to every model, so the tool list doesn't change between models and the cache keeps hitting. A `meme_view` result only carries a marker, `[Sticker image meme=<id>]`, which request rendering (`llm/media.py`) handles for the model in use: a model that accepts image input gets a message with the image attached after that batch of tool results (the most recent `vision.max_images` images); for a model that doesn't, the tool result is an error suggesting it use `meme_search`'s text descriptions instead.

## 5. WebUI

The **Stickers** page has: on/off switches and collection and injection settings; category tag editing (applied with **Save configuration**); a library browser (filter by category, search, pages); uploads (category and description can be left empty to be judged and described automatically); and per-sticker editing of category and description, re-describing, and deleting. WebUI uploads skip the persona judgment and aren't limited by capacity.

API: `GET /api/memes`, `POST /api/memes` (the request body is the image), `GET /api/memes/{id}/image`, `PUT /api/memes/{id}`, `POST /api/memes/{id}/describe`, `DELETE /api/memes/{id}`.
