import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from meow.infrastructure.test_runner import _probe


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_readiness_probe_bypasses_proxy_settings():
    server = HTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    unreachable_proxy = "http://127.0.0.1:9"
    try:
        with patch.dict(
            "os.environ",
            {"HTTP_PROXY": unreachable_proxy, "http_proxy": unreachable_proxy},
        ):
            assert _probe(url) is None
    finally:
        server.shutdown()
        server.server_close()
