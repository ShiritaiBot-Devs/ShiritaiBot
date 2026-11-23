"""Images in user messages, rendered per model at request time.

History keeps the adapter's text markers; image-capable models receive the recent images
themselves, others a cached caption from the vision route or a placeholder.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from shiritaibot.adapters.onebot11 import qq_download_url
from shiritaibot.core import PublicError, canonical
from shiritaibot.core.logs import get_logger
from shiritaibot.core.text import IMAGE_REF
from shiritaibot.i18n import DEFAULT_LOCALE, Text, active_locale, t
from shiritaibot.prompts import prompt

log = get_logger("llm.log.mediaSource")

# meme_view results carry this marker; rendering attaches the image or reports an error.
MEME_VIEW = re.compile(r"\[(?:表情图片|Sticker image) meme=([0-9a-f]{16})\]")  # i18n: ok
# Base64 of this stays within the strictest provider per-image limit (5 MB).
MAX_IMAGE_BYTES = 3_750_000
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024


def sniff(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_part(url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url}}


def view_marker(key: str) -> str:
    return t("markers.stickerImage", key=key)


def prepare_image(data: bytes) -> bytes | None:
    mime = sniff(data)
    if mime is None:
        return None
    animated = mime == "image/gif" or (mime == "image/webp" and b"ANIM" in data[:64])
    if not animated and len(data) <= MAX_IMAGE_BYTES:
        return data

    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as image:
            image.thumbnail((2048, 2048))
            frame = ImageOps.exif_transpose(image).convert("RGBA")
            out = io.BytesIO()
            frame.save(out, "PNG", optimize=True)
            if out.tell() <= MAX_IMAGE_BYTES:
                return out.getvalue()
            # Keep transparency for ordinary images, flatten it only if lossless output is too big.
            rgb = Image.new("RGB", frame.size, "white")
            rgb.paste(frame, mask=frame.getchannel("A"))
            rgb.thumbnail((2048, 2048))
            for quality in (85, 65):
                out = io.BytesIO()
                rgb.save(out, "JPEG", quality=quality, optimize=True)
                if out.tell() <= MAX_IMAGE_BYTES:
                    return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        pass
    return None


def model_image(data: bytes) -> str | None:
    """A data URL for model input, or None when the bytes are not a supported image."""
    prepared = prepare_image(data)
    if prepared is None:
        return None
    return f"data:{sniff(prepared)};base64,{base64.b64encode(prepared).decode()}"


class ImageResolver:
    """One session's image refs for one call; downloads and captions persist across calls."""

    def __init__(self, llm, scope: str, settings):
        self.llm, self.scope, self.cfg = llm, scope, settings
        self.images: dict[str, str | None] = {}
        self.captions: dict[str, str | None] = {}

    @staticmethod
    def present(messages: list[dict]) -> bool:
        return any(
            isinstance(m.get("content"), str)
            and (
                (ImageResolver.qq_message(m) and IMAGE_REF.search(m["content"]))
                or (m["role"] == "tool" and MEME_VIEW.search(m["content"]))
            )
            for m in messages
        )

    @staticmethod
    def qq_message(message: dict) -> bool:
        return message["role"] == "user" or (
            message["role"] == "tool" and message.get("_tool_name") == "group_context_query"
        )

    async def meme(self, key: str) -> str | None:
        """A stored meme as a model-ready data URL; memes are global, not session-bound."""
        if key not in self.images:
            try:
                data = await asyncio.to_thread((self.llm.store.home / "memes" / key).read_bytes)
            except OSError:
                data = None
            self.images[key] = await asyncio.to_thread(model_image, data) if data else None
        return self.images[key]

    async def image(self, ref: str) -> str | None:
        """A data URL, or None when the ref is foreign, expired or not an image."""
        if ref not in self.images:
            self.images[ref] = await self._load(ref)
        return self.images[ref]

    async def _load(self, ref: str) -> str | None:
        # Refs stay bound to their session even after the bytes are cached.
        row = await self.llm.store.get("qq_file", ref)
        if not row or row["scope"] != self.scope:
            return None
        path = self.llm.store.home / "media" / ref
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except OSError:
            data = await self._download(row["data"].get("url", ""))
            if data is None:
                return None

            # QQ CDN links expire, so later turns read the local copy.
            def cache():
                path.parent.mkdir(parents=True, exist_ok=True)
                with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temp = Path(stream.name)
                try:
                    temp.write_bytes(data)
                    temp.replace(path)
                finally:
                    temp.unlink(missing_ok=True)

            try:
                await asyncio.to_thread(cache)
            except OSError:
                log.warning("llm.media.log.cacheFailed")
        return await asyncio.to_thread(model_image, data)

    async def _download(self, url: str) -> bytes | None:
        if not qq_download_url(url):
            return None
        chunks, size = [], 0
        try:
            async with self.llm.client.stream("GET", url, timeout=30) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes(65536):
                    size += len(chunk)
                    if size > MAX_DOWNLOAD_BYTES:
                        log.warning("llm.media.log.tooLarge")
                        return None
                    chunks.append(chunk)
        except httpx.HTTPError as exc:
            log.warning(Text("llm.media.log.downloadFailed", name=type(exc).__name__))
            return None
        prepared = await asyncio.to_thread(prepare_image, b"".join(chunks))
        if prepared is None:
            log.warning("llm.media.log.unsupported")
        return prepared

    async def caption(self, ref: str) -> str | None:
        if not self.cfg.vision.caption or not self.cfg.routes.get("vision"):
            return None
        if ref not in self.captions:
            self.captions[ref] = await self._caption(ref)
        return self.captions[ref]

    async def _caption(self, ref: str) -> str | None:
        # Captions are written in the conversation's language (docs/I18N.md).
        locale = active_locale()
        cached = await self.llm.store.get("image_caption", ref)
        if (
            cached
            and cached["scope"] == self.scope
            and cached.get("locale", DEFAULT_LOCALE) == locale
        ):
            return cached["text"]
        url = await self.image(ref)
        if url is None:
            return None
        try:
            reply = await self.llm.complete(
                "vision",
                prompt("IMAGE_CAPTION", locale),
                [
                    {
                        "role": "user",
                        "content": [
                            image_part(url),
                            {"type": "text", "text": prompt("IMAGE_CAPTION_REQUEST", locale)},
                        ],
                    }
                ],
                [],
                settings=self.cfg,
            )
        except PublicError as exc:
            log.warning(Text("llm.media.log.captionFailed", exc=exc), "vision")
            return None
        text = " ".join(reply.text.split())[:1000]
        if text:
            await self.llm.store.put(
                "image_caption", ref, {"text": text, "locale": locale}, self.scope
            )
        return text or None

    async def render(self, messages: list[dict], model) -> list[dict]:
        native = "image" in model.modalities
        refs = [
            match[1]
            for m in messages
            if self.qq_message(m) and isinstance(m.get("content"), str)
            for match in IMAGE_REF.finditer(m["content"])
        ]
        views = [
            match[1]
            for m in messages
            if m["role"] == "tool" and isinstance(m.get("content"), str)
            for match in MEME_VIEW.finditer(m["content"])
        ]
        limit = self.cfg.vision.max_images
        recent = set(list(dict.fromkeys(reversed(refs)))[:limit]) if native else set()
        recent_views = set(list(dict.fromkeys(reversed(views)))[:limit]) if native else set()
        result: list[dict] = []
        # Viewed memes follow the whole tool-result group as one user message, so
        # providers still see every tool result right after its call.
        attached: list[dict] = []
        rendered_refs: set[str] = set()

        def flush() -> None:
            if attached:
                result.append({"role": "user", "content": list(attached)})
                attached.clear()

        async def render_text(content: str, tool_result: bool = False) -> str | list[dict]:
            parts, text, end = [], "", 0
            for match in IMAGE_REF.finditer(content):
                ref = match[1]
                text += content[end : match.start()]
                end = match.end()
                label = t("markers.imageLabel", ref=ref)
                if ref in rendered_refs:
                    text += label
                    continue
                url = await self.image(ref) if ref in recent else None
                if url:
                    rendered_refs.add(ref)
                    if tool_result:
                        attached.extend([{"type": "text", "text": label}, image_part(url)])
                        text += label
                    else:
                        parts.extend([{"type": "text", "text": text + label}, image_part(url)])
                        text = ""
                    continue
                caption = await self.caption(ref)
                text += t("markers.imageCaption", ref=ref, caption=caption or t("llm.media.noImage"))
            text += content[end:]
            if not parts:
                return text
            if text.strip():
                parts.append({"type": "text", "text": text})
            return parts

        for message in messages:
            content = message.get("content")
            if message["role"] != "tool":
                flush()
            if message["role"] == "tool" and isinstance(content, str) and MEME_VIEW.search(content):
                if not native:
                    result.append({**message, "content": canonical({"error": t("llm.media.noVision")})})
                    continue
                notes: dict[str, str] = {}
                for key in dict.fromkeys(MEME_VIEW.findall(content)):
                    url = await self.meme(key) if key in recent_views else None
                    if url:
                        notes[key] = t("markers.stickerAttached", key=key)
                        attached += [
                            {"type": "text", "text": t("markers.stickerViewed", key=key)},
                            image_part(url),
                        ]
                    else:
                        notes[key] = t("markers.stickerDropped", key=key)
                # Replace the stored marker in whichever language it was written.
                content = MEME_VIEW.sub(lambda match: notes[match[1]], content)
                result.append({**message, "content": content})
                continue
            if (
                not self.qq_message(message)
                or not isinstance(content, str)
                or not IMAGE_REF.search(content)
            ):
                result.append(message)
                continue
            if message["role"] == "tool":
                try:
                    payload = json.loads(content)
                except json.JSONDecodeError:
                    result.append(message)
                    continue
                for row in payload.get("messages", []):
                    row["text"] = await render_text(row["text"], tool_result=True)
                result.append({**message, "content": canonical(payload)})
                continue
            result.append({**message, "content": await render_text(content)})
        flush()
        return result
