#!/usr/bin/env python3
"""Serve the swarm frontend and its data bundle (stdlib only).

  /            -> --dist (the built frontend; unknown paths without a file extension fall back to index.html).
                  Until --dist holds an index.html, a placeholder page with the bundle counts.
  /data/...    -> --data (the bundle; read-only; JSON gets Cache-Control: max-age=60; gzip when accepted)
  /api/health  -> {"ok", "generated", "counts", "dist"}

Only GET and HEAD. Nothing outside the two folders is ever served: paths are decoded, normalised and
resolved (symlinks included) and must stay inside their folder; that check runs on the final path of every
read (directory index.html, SPA fallback, the manifest behind /api/health). --data may be a symlink to the
current bundle generation: it is resolved on every request, so a rebuild swaps it without a restart.
"""
from __future__ import annotations

import argparse
import email.utils
import gzip
import html
import json
import mimetypes
import os
import posixpath
import sys
import threading
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CITE = "Data: AI Digest / AI Village"
COMPRESSIBLE = (".json", ".html", ".js", ".mjs", ".css", ".svg", ".txt", ".map", ".webmanifest", ".xml")
TYPES = {".json": "application/json; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8",
         ".map": "application/json; charset=utf-8", ".webmanifest": "application/manifest+json",
         ".woff2": "font/woff2", ".wasm": "application/wasm"}
GZIP_CACHE_MAX = 96 * 1024 * 1024


def safe_path(root: str, rel: str) -> str | None:
    """Map a URL path below `root` to a real file path inside `root`, or None."""
    if "\x00" in rel or "\\" in rel:
        return None
    norm = posixpath.normpath("/" + rel).lstrip("/")
    if norm.startswith("..") or "/../" in f"/{norm}/":
        return None
    root_real = os.path.realpath(root)
    path = os.path.realpath(os.path.join(root_real, norm)) if norm not in ("", ".") else root_real
    if path != root_real and not path.startswith(root_real + os.sep):
        return None
    return path


class GzipCache:
    def __init__(self):
        self.lock = threading.Lock()
        self.items: dict[tuple, bytes] = {}
        self.size = 0

    def get(self, path: str, st: os.stat_result) -> bytes:
        key = (path, st.st_mtime_ns, st.st_size)
        with self.lock:
            hit = self.items.get(key)
        if hit is not None:
            return hit
        with open(path, "rb") as f:
            data = gzip.compress(f.read(), compresslevel=6, mtime=0)
        with self.lock:
            if self.size + len(data) > GZIP_CACHE_MAX:
                self.items.clear()
                self.size = 0
            self.items[key] = data
            self.size += len(data)
        return data


class App:
    def __init__(self, data: str, dist: str):
        self.data = os.path.abspath(data)
        self.dist = os.path.abspath(dist)
        self.gz = GzipCache()
        self._manifest = (None, None)

    def manifest(self) -> dict | None:
        path = safe_path(self.data, "bundle.json")
        if path is None:
            return None
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = (st.st_mtime_ns, st.st_size)
        if self._manifest[0] != key:
            try:
                with open(path, "rb") as f:
                    self._manifest = (key, json.loads(f.read()).get("manifest") or {})
            except (OSError, ValueError):
                return None
        return self._manifest[1]

    def index(self) -> str | None:
        """dist/index.html, resolved and inside dist, or None."""
        path = safe_path(self.dist, "index.html")
        return path if path is not None and os.path.isfile(path) else None

    def has_dist(self) -> bool:
        return self.index() is not None


def placeholder(manifest: dict | None) -> bytes:
    rows = ""
    if manifest:
        for k, v in (manifest.get("counts") or {}).items():
            rows += f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>"
        rng = manifest.get("range") or {}
        span = f"Days {rng.get('first_day')} to {rng.get('last_day')} ({rng.get('first_date')} to {rng.get('last_date')})"
        gen = f"Bundle generated {html.escape(str(manifest.get('generated')))}."
    else:
        span, gen = "No bundle yet.", ""
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Swarm memory</title>
<style>body{{font:15px/1.5 system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem;color:#222;background:#fafaf7}}
table{{border-collapse:collapse}}td{{padding:.2rem 1rem .2rem 0;border-bottom:1px solid #ddd}}
td+td{{text-align:right;font-variant-numeric:tabular-nums}}small{{color:#666}}</style></head><body>
<h1>Swarm memory</h1><p>The app is being built. The data bundle is live at <a href="/data/bundle.json">/data/bundle.json</a>.</p>
<p>{html.escape(span)}</p><table>{rows}</table><p><small>{gen}</small></p>
<p>{html.escape(CITE)} (<a href="https://theaidigest.org/village">theaidigest.org/village</a>). Research use only.</p>
</body></html>"""
    return page.encode()


class Handler(BaseHTTPRequestHandler):
    server_version = "swarm-serve"
    sys_version = ""
    timeout = 30
    app: App = None  # set by make_server

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def do_GET(self):
        self.route(head=False)

    def do_HEAD(self):
        self.route(head=True)

    def do_POST(self):
        self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    def route(self, head: bool):
        raw = urllib.parse.urlsplit(self.path).path
        try:
            path = urllib.parse.unquote(raw, errors="strict") if "%" in raw else raw
        except UnicodeDecodeError:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        if path == "/api/health":
            m = self.app.manifest()
            body = json.dumps({"ok": m is not None, "generated": (m or {}).get("generated"),
                               "counts": (m or {}).get("counts"), "dist": self.app.has_dist()}).encode()
            return self.send_bytes(body, "application/json; charset=utf-8", head, cache="no-store")
        if path == "/data" or path.startswith("/data/"):
            target = safe_path(self.app.data, path[len("/data"):])
            if target is None or not os.path.isfile(target):
                return self.send_error(HTTPStatus.NOT_FOUND)
            return self.send_file(target, head, cache="public, max-age=60")
        if not self.app.has_dist():
            if path in ("/", "/index.html"):
                return self.send_bytes(placeholder(self.app.manifest()), TYPES[".html"], head, cache="no-store")
            return self.send_error(HTTPStatus.NOT_FOUND)
        target = safe_path(self.app.dist, path)
        if target is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        if os.path.isdir(target):  # the directory's index.html, checked again after resolving
            target = safe_path(self.app.dist, posixpath.join(path, "index.html"))
            if target is None:
                return self.send_error(HTTPStatus.NOT_FOUND)
        if os.path.isfile(target):
            immutable = path.startswith("/assets/")
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return self.send_file(target, head, cache=cache)
        if "." in posixpath.basename(path):
            return self.send_error(HTTPStatus.NOT_FOUND)
        index = self.app.index()
        if index is None:
            return self.send_error(HTTPStatus.NOT_FOUND)
        return self.send_file(index, head, cache="no-cache")

    def send_file(self, path: str, head: bool, cache: str):
        try:
            st = os.stat(path)
        except OSError:
            return self.send_error(HTTPStatus.NOT_FOUND)
        ext = os.path.splitext(path)[1].lower()
        ctype = TYPES.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ext == ".json" and cache.startswith("public"):
            cache = "public, max-age=60"
        last_mod = email.utils.formatdate(st.st_mtime, usegmt=True)
        ims = self.headers.get("If-Modified-Since")
        if ims:
            try:
                if int(st.st_mtime) <= email.utils.parsedate_to_datetime(ims).timestamp():
                    self.send_response(HTTPStatus.NOT_MODIFIED)
                    self.send_header("Last-Modified", last_mod)
                    self.send_header("Cache-Control", cache)
                    self.end_headers()
                    return
            except (TypeError, ValueError):
                pass
        gz = ext in COMPRESSIBLE and "gzip" in (self.headers.get("Accept-Encoding") or "")
        if gz:
            body = self.app.gz.get(path, st)
        else:
            with open(path, "rb") as f:
                body = f.read()
        self.send_bytes(body, ctype, head, cache=cache, gz=gz, last_mod=last_mod, vary=ext in COMPRESSIBLE)

    def send_bytes(self, body: bytes, ctype: str, head: bool, cache: str, gz: bool = False,
                   last_mod: str | None = None, vary: bool = False):
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if gz:
            self.send_header("Content-Encoding", "gzip")
        if vary:
            self.send_header("Vary", "Accept-Encoding")
        if last_mod:
            self.send_header("Last-Modified", last_mod)
        self.end_headers()
        if not head:
            self.wfile.write(body)


def make_server(host: str, port: int, data: str, dist: str) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": App(data, dist)})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data", default="/home/lexi/calc-nvme/hackathon-20261004/bundle/data")
    ap.add_argument("--dist", default="/home/lexi/calc-nvme/hackathon-20261004/web-dist")
    args = ap.parse_args(argv)
    srv = make_server(args.host, args.port, args.data, args.dist)
    print(f"serving http://{args.host}:{args.port}/  data={args.data}  dist={args.dist}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
