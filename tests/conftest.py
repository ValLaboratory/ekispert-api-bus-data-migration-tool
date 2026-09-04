import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

EXAMPLES_DIR = os.path.join(os.path.dirname(__file__), "..", "examples")


@pytest.fixture
def start_server():
    """ローカルHTTPサーバーによるモックAPIサーバーを起動し、ベースURLを返す。

    handler(path, query) -> (status, body)
    path はリクエストのパス、query はクエリ文字列を dict（値は最初の1件）にしたもの。
    """
    servers = []

    def _start(handler):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urlparse(self.path)
                q = parse_qs(parsed.query)
                query = {k: v[0] for k, v in q.items()}
                status, body = handler(parsed.path, query)
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(body.encode("utf-8"))

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        host, port = srv.server_address
        return "http://%s:%d" % (host, port)

    yield _start
    for srv in servers:
        srv.shutdown()
        srv.server_close()
