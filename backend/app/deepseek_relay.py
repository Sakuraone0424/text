"""One-request loopback relay: official HTTPS only, no redirects, no extra uploads."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json, os, threading
from pathlib import Path
from app.provider_diagnostics import inspect_sse, private_write
import httpx


@contextmanager
def relay(model, token_limit, request_guard=None, evidence_dir=None, request_timeout=120):
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
            if request_guard is not None:
                try:
                    request_guard(body)
                except ValueError:
                    self.send_error(403, "Unapproved request body")
                    return
            with lock:
                if state["requests"] >= 1:
                    self.send_error(429)
                    return
                state["requests"] += 1
            chunks = []
            response_file = None
            try:
                wire = json.dumps(body, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
                state["request_sha256"] = hashlib.sha256(wire).hexdigest()
                if evidence_dir is not None:
                    directory = Path(evidence_dir)
                    private_write(directory / "request.json", wire)
                    fd = os.open(directory / "response.sse", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    response_file = os.fdopen(fd, "wb")
                with httpx.Client(timeout=request_timeout, follow_redirects=False) as client:
                    with client.stream(
                        "POST",
                        "https://api.deepseek.com/anthropic/v1/messages",
                        headers={
                            "x-api-key": os.environ["DEEPSEEK_API_KEY"],
                            "anthropic-version": "2023-06-01",
                            "Content-Type": "application/json",
                        },
                        content=wire,
                    ) as response:
                        state["http_status"] = response.status_code
                        total = 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > (8 if token_limit > 4096 else 2) * 1024**2:
                                raise ValueError("response_too_large")
                            chunks.append(chunk)
                            if response_file is not None:
                                response_file.write(chunk)
                                response_file.flush()
                                os.fsync(response_file.fileno())
                        raw = b"".join(chunks)
                        status = response.status_code
                        state["http_status"] = status
                state.update(inspect_sse(raw))
                if evidence_dir is not None:
                    private_write(Path(evidence_dir) / "receipt.json", json.dumps(state).encode())
                self.send_response(status)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception:
                state.update(inspect_sse(b"".join(chunks)))
                self.send_error(502, "Official request failed")
            finally:
                if response_file is not None:
                    response_file.close()

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
