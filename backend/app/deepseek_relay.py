"""One-request loopback relay: official HTTPS only, no redirects, no extra uploads."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, threading
import httpx


@contextmanager
def relay(model, token_limit):
    state = {
        "requests": 0,
        "usage": {},
        "model_version": None,
        "returned_model_id": None,
        "http_status": None,
    }
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_error(403)

        def do_POST(self):
            size = int(self.headers.get("content-length", "0"))
            if self.path != "/v1/messages" or size <= 0 or size > 32000:
                self.send_error(403)
                return
            body = json.loads(self.rfile.read(size))
            if (
                body.get("model") != model
                or body.get("max_tokens", 0) > token_limit
                or any(
                    body.get(k)
                    for k in (
                        "tools",
                        "tool_choice",
                        "server_tools",
                        "web_search",
                        "search",
                        "fetch",
                        "enable_search",
                        "plugins",
                    )
                )
                or any("session_log" in k or k.startswith("dsh_") for k in body)
            ):
                self.send_error(403)
                return
            with lock:
                if state["requests"] >= 1:
                    self.send_error(429)
                    return
                state["requests"] += 1
            try:
                with httpx.Client(timeout=120, follow_redirects=False) as client:
                    with client.stream(
                        "POST",
                        "https://api.deepseek.com/anthropic/v1/messages",
                        headers={
                            "x-api-key": os.environ["DEEPSEEK_API_KEY"],
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        json=body,
                    ) as response:
                        chunks = []
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > 2 * 1024**2:
                                raise ValueError("response_too_large")
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        status = response.status_code
                        state["http_status"] = status
                if status == 200:
                    for line in raw.decode().splitlines():
                        if not line.startswith("data: "):
                            continue
                        try:
                            item = json.loads(line[6:])
                        except ValueError:
                            continue
                        message = item.get("message", {})
                        usage = message.get("usage") or item.get("usage", {})
                        for key in (
                            "input_tokens",
                            "output_tokens",
                            "cache_read_input_tokens",
                            "cache_creation_input_tokens",
                        ):
                            if type(usage.get(key)) is int and usage[key] >= 0:
                                state["usage"][key] = usage[key]
                        if message.get("model"):
                            state["returned_model_id"] = message["model"]
                        if isinstance(message.get("model_version"), str):
                            state["model_version"] = message["model_version"]
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                self.send_error(502, "Official request failed")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
