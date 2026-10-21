#!/usr/bin/env python3
"""Mock keypackage/account registry for the chat-cli CI smoketest and tests.

A path-keyed store: each POST body is kept under its path, and a GET to the
same path returns it.

  * POST (any)  -> 200, body stored under the path, or under the path its
                   `device_id` names when it is a keypackage
  * GET  (any)  -> 200 with the stored body, or 404 if nothing is stored there

Account logs are posted and fetched at the same `/v1/account/<addr>` path, so
the client reads back what it published. Keypackages post to `/v0/keypackage`
and are fetched from `/v0/keypackage/<device_id>`, so that POST is stored under
the path its `device_id` names, and the fetch returns the `payload` and
`signature` it was posted with. One that names no device is stored under the
path it was posted to, like any other POST. Threaded because the client holds
one keep-alive connection per HTTP client. This validates nothing — it only
unblocks the smoketest; protocol-level behavior is covered by the workspace tests.

Serves on 127.0.0.1 unless `--bind` names another address, port 18080 unless
`--port` names another (0 for one the system picks), and prints the address as
its first line of standard output.
With `--requests FILE` it appends a line of JSON to the file for each request it
serves: when it arrived, in seconds since the epoch, its method, path and status.
"""

import argparse
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYPACKAGE_PATH = "/v0/keypackage"


class Handler(BaseHTTPRequestHandler):
    # Match the client's HTTP/1.1 requests so reqwest frames the response body.
    protocol_version = "HTTP/1.1"
    # A reply is written as its headers and then its body. On a kept connection
    # the body would wait for the client to acknowledge the headers, 40 ms.
    disable_nagle_algorithm = True
    store = {}  # path -> last body POSTed there
    requests = None  # the file each request is written to, when one was named
    requests_lock = threading.Lock()

    def _drain(self):
        # Consume the request body so the client's request completes cleanly.
        length = int(self.headers.get("Content-Length", 0))
        if length:
            self.rfile.read(length)

    def _reply(self, status):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _note(self, arrived, status):
        if self.requests is None:
            return
        line = json.dumps(
            {"at": arrived, "method": self.command, "path": self.path, "status": status}
        )
        with self.requests_lock:
            self.requests.write(line + "\n")
            self.requests.flush()

    def do_POST(self):
        arrived = time.time()
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        path = self.path
        if path == KEYPACKAGE_PATH:
            try:
                path = f"{KEYPACKAGE_PATH}/{json.loads(body)['device_id']}"
            except (ValueError, KeyError, TypeError):
                pass
        self.store[path] = body
        self._note(arrived, 200)
        self._reply(200)

    def do_GET(self):
        arrived = time.time()
        self._drain()
        body = self.store.get(self.path)
        if body is None:
            self._note(arrived, 404)
            return self._reply(404)
        self._note(arrived, 200)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class Registry(ThreadingHTTPServer):
    # Every member of a group looks the sender of a post up at once.
    request_queue_size = socket.SOMAXCONN


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--bind", metavar="ADDRESS", default="127.0.0.1", help="the address to serve on"
    )
    parser.add_argument(
        "--port", type=int, default=18080, help="0 for one the system picks"
    )
    parser.add_argument(
        "--requests", metavar="FILE", help="append a line for each request served"
    )
    args = parser.parse_args()
    if args.requests:
        Handler.requests = open(args.requests, "a", encoding="utf-8")
    server = Registry((args.bind, args.port), Handler)
    print(f"http://{args.bind}:{server.server_port}", flush=True)
    server.serve_forever()
