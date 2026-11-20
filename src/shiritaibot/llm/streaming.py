"""Collect provider SSE replies for the existing, whole-reply agent interface."""

from __future__ import annotations

import json
import time

import httpx


async def _events(response: httpx.Response):
    # All events delivered in one network chunk share its arrival time, so a
    # buffering gateway cannot turn JSON parsing time into apparent token speed.
    pending = ""
    lines = []
    saw_cr = False
    async for chunk in response.aiter_text():
        arrived = time.monotonic()
        if saw_cr:
            chunk = chunk.removeprefix("\n")
        saw_cr = chunk.endswith("\r")
        pieces = (pending + chunk.replace("\r\n", "\n").replace("\r", "\n")).split("\n")
        pending = pieces.pop()
        for line in pieces:
            if line.startswith("data:"):
                lines.append(line[5:].removeprefix(" "))
            elif not line and lines:
                yield "\n".join(lines), arrived
                lines.clear()


class ChatStream:
    def __init__(self):
        self.message: dict = {"role": "assistant"}
        self.calls = {}
        self.usage = {}
        self.finished = False

    def add(self, event):
        if event.get("usage"):
            self.usage = event["usage"]
        output = False
        for choice in event.get("choices", []):
            if choice.get("index", 0) != 0:
                continue
            self.finished |= bool(choice.get("finish_reason"))
            delta = choice.get("delta") or {}
            for key in ("content", "reasoning_content", "refusal"):
                if delta.get(key):
                    self.message[key] = self.message.get(key, "") + delta[key]
                    output = True
            for part in delta.get("tool_calls") or []:
                call = self.calls.setdefault(
                    part["index"],
                    {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                )
                if part.get("id"):
                    call["id"] = part["id"]
                for key in ("name", "arguments"):
                    if value := (part.get("function") or {}).get(key):
                        call["function"][key] += value
                        output = True
        return output

    def result(self):
        if self.calls:
            self.message["tool_calls"] = [self.calls[i] for i in sorted(self.calls)]
        return {"choices": [{"message": self.message}], "usage": self.usage}


class ResponsesStream:
    def __init__(self):
        self.response = {}
        self.finished = False

    def add(self, event):
        kind = event["type"]
        if kind in {"response.failed", "response.cancelled"}:
            raise ValueError("response failed")
        if kind in {"response.completed", "response.incomplete"}:
            self.response = event["response"]
            self.finished = True
        return kind in {
            "response.output_text.delta",
            "response.refusal.delta",
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
            "response.function_call_arguments.delta",
        } and bool(event.get("delta"))

    def result(self):
        return self.response


class AnthropicStream:
    def __init__(self):
        self.blocks = {}
        self.arguments = {}
        self.usage = {}
        self.finished = False

    def add(self, event):
        kind = event["type"]
        if kind == "message_start":
            self.usage.update(event["message"].get("usage", {}))
        elif kind == "content_block_start":
            block = event["content_block"]
            self.blocks[event["index"]] = block
            return bool(block.get("text") or block.get("thinking") or block.get("name"))
        elif kind == "content_block_delta":
            index, delta = event["index"], event["delta"]
            block = self.blocks[index]
            if "partial_json" in delta:
                self.arguments[index] = self.arguments.get(index, "") + delta["partial_json"]
                return bool(delta["partial_json"])
            if "citation" in delta:
                block.setdefault("citations", []).append(delta["citation"])
            for key in ("text", "thinking", "signature"):
                if key in delta:
                    block[key] = block.get(key, "") + delta[key]
                    return key != "signature" and bool(delta[key])
        elif kind == "message_delta":
            self.usage.update(event.get("usage") or {})
        elif kind == "message_stop":
            self.finished = True
        return False

    def result(self):
        for index, arguments in self.arguments.items():
            if arguments:
                self.blocks[index]["input"] = json.loads(arguments)
        return {"content": [self.blocks[i] for i in sorted(self.blocks)], "usage": self.usage}


class GeminiStream:
    def __init__(self):
        self.parts = []
        self.usage = {}
        self.finished = False

    def add(self, event):
        self.usage.update(event.get("usageMetadata") or {})
        output = False
        for candidate in event.get("candidates", []):
            if candidate.get("index", 0) != 0:
                continue
            parts = candidate.get("content", {}).get("parts", [])
            self.parts.extend(parts)
            self.finished |= bool(candidate.get("finishReason"))
            output |= any(part.get("text") or part.get("functionCall") for part in parts)
        return output

    def result(self):
        return {"candidates": [{"content": {"parts": self.parts}}], "usageMetadata": self.usage}


async def collect_stream(protocol, response, started):
    collector = {
        "chat_completions": ChatStream,
        "responses": ResponsesStream,
        "anthropic": AnthropicStream,
        "gemini": GeminiStream,
    }[protocol]()
    first = last = None
    async for payload, arrived in _events(response):
        if payload == "[DONE]":
            break
        event = json.loads(payload)
        if event.get("error") or event.get("type") == "error":
            raise ValueError("provider stream error")
        if collector.add(event):
            last = arrived
            if first is None:
                first = last
    if not collector.finished:
        raise ValueError("incomplete provider stream")
    timing = {}
    if first is not None and last is not None:
        timing["ttft_ms"] = (first - started) * 1000
        if last > first:
            timing["generation_seconds"] = last - first
    return collector.result(), timing
