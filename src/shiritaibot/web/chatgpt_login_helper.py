"""Run on the computer opening the browser. Uses only the Python standard library."""

from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from secrets import compare_digest
from time import monotonic
from urllib.parse import parse_qs, urlsplit
from webbrowser import open as open_browser


def main(authorization_url: str) -> None:
    params = parse_qs(urlsplit(authorization_url).query)
    state = params["state"][0]
    redirect = urlsplit(params["redirect_uri"][0])
    port = redirect.port
    if redirect.scheme != "http" or redirect.hostname != "127.0.0.1" or port is None:
        raise SystemExit("Invalid localhost callback.")
    result = ""

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            nonlocal result
            self.connection.settimeout(5)
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
            result = redirect._replace(query=uri.query).geturl()
            page = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>返回 ShiritaiBot</title>
<style>
body {{max-width: 42rem; margin: 12vh auto; padding: 1rem; font: 1rem/1.65 system-ui, sans-serif}}
textarea {{box-sizing: border-box; width: 100%; padding: .65rem; margin-bottom: .75rem}}
button {{font: inherit; padding: .35rem .7rem}}
</style>
<h1>返回 ShiritaiBot</h1>
<p>复制下面的一次性回调地址，粘贴回 WebUI 的 ChatGPT 授权面板。</p>
<textarea id="callback" rows="4" readonly aria-label="一次性回调地址">{escape(result)}</textarea>
<button id="copy">复制回调地址</button>
<script>
history.replaceState(null, "", location.pathname);
document.querySelector("#copy").onclick = async (event) => {{
  const field = document.querySelector("#callback");
  try {{
    await navigator.clipboard.writeText(field.value);
    event.target.textContent = "已复制";
  }} catch {{
    field.select();
    event.target.textContent = "请使用键盘复制";
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

    try:
        server = HTTPServer(("127.0.0.1", port), Callback)
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
        while not result and monotonic() < deadline:
            server.handle_request()
    if not result:
        raise SystemExit("Sign-in timed out. Start a new sign-in in ShiritaiBot.")
