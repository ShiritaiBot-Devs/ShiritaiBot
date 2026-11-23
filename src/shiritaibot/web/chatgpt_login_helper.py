"""Run on the computer opening the browser. Uses only the Python standard library."""

import json
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from secrets import compare_digest
from threading import Event
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from webbrowser import open as open_browser

# The WebUI replaces this line with the viewer's language when it serves the script.
TEXT = {"lang": "zh-CN", "title": "返回 ShiritaiBot", "intro": "复制下面的一次性回调地址，粘贴回 WebUI 的 ChatGPT 授权面板。", "label": "一次性回调地址", "copy": "复制回调地址", "copied": "已复制", "manual": "请使用键盘复制"}  # i18n: ok


def script_text(value: str) -> str:
    """A JavaScript string literal that is safe inside <script>. HTML entities are not
    decoded there, so html.escape would break the script; escaping <, > and & keeps
    "</script>" from ending it early."""
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def main(authorization_url: str) -> None:
    params = parse_qs(urlsplit(authorization_url).query)
    state = params["state"][0]
    redirect = urlsplit(params["redirect_uri"][0])
    port = redirect.port
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or port is None:
        raise SystemExit("Invalid localhost callback.")
    completed = Event()

    class Callback(BaseHTTPRequestHandler):
        # StreamRequestHandler applies this timeout before reading the request line.
        timeout = 5

        def log_message(self, *_args):
            pass

        def do_GET(self):
            try:
                uri = urlsplit(self.path)
                query = parse_qs(uri.query, keep_blank_values=True, max_num_fields=24)
                valid = (
                    self.headers.get("Host") == redirect.netloc
                    and not uri.netloc
                    and uri.path == redirect.path
                    and query.get("code", query.get("error"))
                    and len(query.get("state", [])) == 1
                    and compare_digest(query["state"][0].encode(), state.encode())
                )
            except ValueError:
                valid = False
            if not valid:
                self.send_error(400, "This callback does not match the current ChatGPT sign-in.")
                return
            callback_url = redirect._replace(query=uri.query).geturl()
            page = f"""<!doctype html>
<html lang="{escape(TEXT['lang'])}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(TEXT['title'])}</title>
<style>
body {{max-width: 42rem; margin: 12vh auto; padding: 1rem; font: 1rem/1.65 system-ui, sans-serif}}
textarea {{box-sizing: border-box; width: 100%; padding: .65rem; margin-bottom: .75rem}}
button {{font: inherit; padding: .35rem .7rem}}
</style>
<h1>{escape(TEXT['title'])}</h1>
<p>{escape(TEXT['intro'])}</p>
<textarea id="callback" rows="4" readonly aria-label="{escape(TEXT['label'])}">{escape(callback_url)}</textarea>
<button id="copy">{escape(TEXT['copy'])}</button>
<script>
history.replaceState(null, "", location.pathname);
document.querySelector("#copy").onclick = async (event) => {{
  const field = document.querySelector("#callback");
  try {{
    await navigator.clipboard.writeText(field.value);
    event.target.textContent = {script_text(TEXT['copied'])};
  }} catch {{
    field.select();
    event.target.textContent = {script_text(TEXT['manual'])};
  }}
}};
</script></html>""".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(page)
            self.wfile.flush()
            completed.set()

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Callback)
    except OSError:
        raise SystemExit(
            "The callback port is unavailable. Start a new sign-in in ShiritaiBot."
        ) from None
    with server:
        print("Keep this window open until ChatGPT returns to the browser.", flush=True)
        if not open_browser(authorization_url):
            print("Open this URL in your browser:", authorization_url, flush=True)
        server.timeout = 1
        deadline = monotonic() + 600
        while not completed.is_set() and monotonic() < deadline:
            server.handle_request()
    if not completed.is_set():
        raise SystemExit("Sign-in timed out. Start a new sign-in in ShiritaiBot.")
