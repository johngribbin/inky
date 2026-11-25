#!/usr/bin/env python3
"""Small OpenAI-compatible pass-through proxy for a local Ollama server.

- Serves the chat UI (chat.html) at /
- Adds a default reasoning_effort to chat requests that don't set one, so coding agents
  that never send it don't fall back to the model's (slow) high-effort default.
- Forwards everything else unchanged, including streaming responses.

Standard library only. Configured by environment variables:
  PROXY_LISTEN    host:port to listen on          (127.0.0.1:8089)
  UPSTREAM        Ollama base URL                 (http://127.0.0.1:11435)
  INKY_MODEL   model name shown in the UI      (inkling-2-mini:latest)
  INKY_EFFORT  default reasoning effort, or "default" to inject nothing (low)
"""
import json, os, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = os.environ.get("PROXY_LISTEN", "127.0.0.1:8089").rsplit(":", 1)
UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:11435").rstrip("/")
MODEL = os.environ.get("INKY_MODEL", "inkling-2-mini:latest")
EFFORT = os.environ.get("INKY_EFFORT", "low")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.html")
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, status, body=b"", ctype=None, headers=()):
        self.send_response(status)
        if ctype:
            self.send_header("Content-Type", ctype)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _local(self):
        path = self.path.split("?")[0]
        if self.command == "GET" and path in ("/", "/index.html"):
            with open(PAGE, "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8", [("Cache-Control", "no-store")])
            return True
        if self.command == "GET" and path == "/ui-config.json":
            cfg = json.dumps({"model": MODEL, "effort": EFFORT if EFFORT != "default" else "high"}).encode()
            self._send(200, cfg, "application/json", [("Cache-Control", "no-store")])
            return True
        if path == "/props":
            # A browser that once used llama-server on this port may still run llama.cpp's cached
            # web UI, which polls /props. Clear the origin's HTTP cache so a reload gets chat.html.
            # Only "cache": saved conversations in browser storage are left alone.
            self._send(404, headers=[("Clear-Site-Data", '"cache"')])
            return True
        return False

    def _forward(self):
        if self._local():
            return
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)) or None
        if (body and self.command == "POST" and EFFORT != "default"
                and self.path.rstrip("/").endswith("/chat/completions")):
            try:
                req_json = json.loads(body)
                kwargs = req_json.get("chat_template_kwargs") or {}
                if not any(k in req_json for k in ("reasoning_effort", "think")) and "reasoning_effort" not in kwargs:
                    req_json["reasoning_effort"] = EFFORT
                body = json.dumps(req_json).encode()
            except ValueError:
                pass
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        req = urllib.request.Request(UPSTREAM + self.path, data=body, headers=headers, method=self.command)
        try:
            resp = urllib.request.urlopen(req, timeout=3600)
        except urllib.error.HTTPError as e:
            resp = e
        except Exception as e:
            msg = json.dumps({"error": {"message": f"upstream unavailable: {e}"}}).encode()
            self._send(502, msg, "application/json")
            return
        self.send_response(resp.status)
        for k, v in resp.headers.items():
            if k.lower() not in HOP:
                self.send_header(k, v)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            resp.close()

    do_GET = do_POST = do_PUT = do_DELETE = _forward

    def log_message(self, fmt, *args):
        sys.stderr.write("proxy: " + (fmt % args) + "\n")


if __name__ == "__main__":
    print(f"proxy: {HOST}:{PORT} -> {UPSTREAM} (model={MODEL}, default reasoning_effort={EFFORT})",
          file=sys.stderr, flush=True)
    ThreadingHTTPServer((HOST, int(PORT)), Handler).serve_forever()
