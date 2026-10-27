"""Real installed Harness SDK against a LOCAL mock server, never DeepSeek API."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import pytest


@pytest.mark.parametrize("effort", ["off", "high"])
def test_official_sdk_wire_tools_and_extra_uploads(tmp_path, effort):
    from deepseek_harness import DeepSeekHarness

    bodies = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            bodies.append(
                json.loads(self.rfile.read(int(self.headers["content-length"])))
            )
            items = [
                dict(
                    type="message_start",
                    message=dict(
                        id="synthetic",
                        model="deepseek-flash",
                        usage=dict(input_tokens=3, output_tokens=0),
                    ),
                ),
                dict(
                    type="content_block_start",
                    index=0,
                    content_block=dict(type="text", text=""),
                ),
                dict(
                    type="content_block_delta",
                    index=0,
                    delta=dict(type="text_delta", text='{"check":2}'),
                ),
                dict(type="content_block_stop", index=0),
                dict(
                    type="message_delta",
                    delta=dict(stop_reason="end_turn"),
                    usage=dict(output_tokens=5),
                ),
                dict(type="message_stop"),
            ]
            raw = "".join(
                "event: " + i["type"] + "\ndata: " + json.dumps(i) + "\n\n"
                for i in items
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    patch = Path(__file__).resolve().parents[1] / "harness" / "restricted.patch.yml"
    try:
        with DeepSeekHarness(
            dsh_home=str(tmp_path / "home"),
            cwd=str(tmp_path),
            profile="sdk-minimal",
            patches=(str(patch),),
            provider="deepseek-official",
            model="deepseek-flash",
            reasoning_effort=effort,
            max_tokens=128,
            api_key="synthetic-local-only-key",
            base_url=f"http://127.0.0.1:{server.server_port}",
            request_timeout_seconds=15,
            env={"DSH_PRIMARY_RUNTIME": "", "OTEL_SDK_DISABLED": "true"},
        ) as harness:
            result = harness.run(
                'Synthetic protocol fixture only: return JSON {"check":2}',
                session_id="synthetic-only",
            )
            assert json.loads(result.final_response) == {"check": 2}
            assert result.finish_reason == "completed"
        assert len(bodies) == 1
        body = bodies[0]
        assert body["thinking"] == {
            "type": "disabled" if effort == "off" else "enabled"
        }
        assert "untrusted" in str(body.get("system", "")).lower()
        assert not body.get("tools")
        assert not any("session_log" in k or k.startswith("dsh_") for k in body)
        if effort == "high":
            assert body.get("output_config") == {"effort": "high"}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
