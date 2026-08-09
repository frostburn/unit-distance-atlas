#!/usr/bin/env python3
"""Serve the atlas locally so browser fetch() calls work."""

from __future__ import annotations

import argparse
import functools
import http.server
import socket
import webbrowser
from pathlib import Path


class AtlasHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # The generator is commonly rerun while the browser remains open.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--open", action="store_true", help="open the atlas in the default browser")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent
    handler = functools.partial(AtlasHandler, directory=str(root))
    server = http.server.ThreadingHTTPServer((args.host, args.port), handler)
    address = f"http://{args.host}:{server.server_port}/"
    print(f"Serving {root}")
    print(address)
    if args.open:
        webbrowser.open(address)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
