#!/usr/bin/env python3
"""Small OpenAI-compatible pass-through proxy for a local Ollama server.

- Serves the chat UI (chat.html) at /
- Adds a default reasoning_effort to chat requests that don't set one, so coding agents
  that never send it don't fall back to the model's (slow) high-effort default.
- Makes reasoning_effort "none" mean no thinking for Inkling models. Their template only sets "effort
  level 0", so the model still thinks, and Ollama drops that thinking from the reply: the client pays for
  it but can't see or cut it. For plain-text chats the proxy renders the prompt itself and starts the
  model's turn as text, so it answers straight away.
- Forwards everything else unchanged, including streaming responses.

Standard library only. Configured by environment variables:
  PROXY_LISTEN    host:port to listen on          (127.0.0.1:8089)
  UPSTREAM        Ollama base URL                 (http://127.0.0.1:11435)
  INKY_MODEL   model name shown in the UI      (inkling-2-mini:latest)
  INKY_EFFORT  default reasoning effort, or "default" to inject nothing (low)
"""
import json, os, sys, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, PORT = os.environ.get("PROXY_LISTEN", "127.0.0.1:8089").rsplit(":", 1)
UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:11435").rstrip("/")
MODEL = os.environ.get("INKY_MODEL", "inkling-2-mini:latest")
EFFORT = os.environ.get("INKY_EFFORT", "low")
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat.html")
HOP = {"connection", "keep-alive", "transfer-encoding", "content-length", "host"}

# Inkling's prompt format, from the model's chat template (role and content tokens). Used only to answer
# reasoning_effort "none" without thinking: the prompt ends by opening the model's turn as text.
ROLE = {"user": "<|message_user|>", "assistant": "<|message_model|>", "system": "<|message_system|>"}
TEXT, THINK, END = "<|content_text|>", "<|content_thinking|>", "<|end_message|>"
NO_THINK_EFFORT = "<|message_system|><|content_text|>Thinking effort level: 0<|end_message|>"
_renderers = {}


def renderer(model):
    """The model's built-in Ollama renderer (e.g. "inkling2"), or "" for a template model. Cached."""
    if model not in _renderers:
        try:
            req = urllib.request.Request(UPSTREAM + "/api/show", data=json.dumps({"model": model}).encode())
            with urllib.request.urlopen(req, timeout=10) as resp:
                _renderers[model] = json.load(resp).get("renderer") or ""
        except Exception:
            return ""
    return _renderers[model]


def text_of(content):
    """A message's text, or None if it has anything but text (images)."""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    parts = []
    for part in content if isinstance(content, list) else [content]:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type", "text") in ("text", "input_text"):
            parts.append(part.get("text") or "")
        else:
            return None
    return "".join(parts)


def no_think_prompt(req):
    """The Inkling prompt for a plain-text chat, ending with the model's turn opened as text; None for
    anything this doesn't cover (tools, images, tool results), which then goes to Ollama unchanged."""
    if req.get("tools") or req.get("reasoning_effort") != "none" or not renderer(req.get("model", "")).startswith("inkling"):
        return None
    out, effort_done = "", False
    for m in req.get("messages") or []:
        role, text = m.get("role"), text_of(m.get("content"))
        if role not in ROLE or text is None or m.get("tool_calls"):
            return None
        if role != "system" and not effort_done:
            out, effort_done = out + NO_THINK_EFFORT, True
        thinking = m.get("reasoning_content") or m.get("reasoning") if role == "assistant" else None
        if thinking:
            out += ROLE[role] + THINK + thinking + END
        if text or not thinking:
            out += ROLE[role] + TEXT + text + END
    return out + ("" if effort_done else NO_THINK_EFFORT) + ROLE["assistant"] + TEXT


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
                req_json = None
            prompt = no_think_prompt(req_json) if isinstance(req_json, dict) else None
            if prompt is not None:
                self._no_think(req_json, prompt)
                return
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

    def _no_think(self, req, prompt):
        """Answer a chat completion from a raw prompt whose model turn is already opened as text, in the
        OpenAI format the client asked for (streamed or not)."""
        opts = {"num_predict": req.get("max_completion_tokens") or req.get("max_tokens")}
        for k in ("temperature", "top_p", "seed"):
            opts[k] = req.get(k)
        stop = req.get("stop")
        opts["stop"] = [stop] if isinstance(stop, str) else stop
        body = {"model": req["model"], "prompt": prompt, "raw": True, "stream": True,
                "options": {k: v for k, v in opts.items() if v is not None}}
        up = urllib.request.Request(UPSTREAM + "/api/generate", data=json.dumps(body).encode(),
                                    headers={"Content-Type": "application/json"})
        stream = bool(req.get("stream"))
        cid, created = f"chatcmpl-{int(time.time() * 1000)}", int(time.time())

        def chunk(delta, finish=None, usage=None):
            ev = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": req["model"],
                  "choices": [] if usage else [{"index": 0, "delta": delta, "finish_reason": finish}]}
            if usage:
                ev["usage"] = usage
            data = b"data: " + json.dumps(ev).encode() + b"\n\n"
            self.wfile.write(b"%x\r\n%s\r\n" % (len(data), data))
            self.wfile.flush()

        try:
            resp = urllib.request.urlopen(up, timeout=3600)
        except Exception as e:
            msg = json.dumps({"error": {"message": f"upstream unavailable: {e}"}}).encode()
            self._send(502, msg, "application/json")
            return
        text, finish, usage = "", "stop", {}
        try:
            if stream:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                chunk({"role": "assistant", "content": ""})
            closed = False
            for line in resp:
                ev = json.loads(line)
                piece = ev.get("response") or ""
                if closed or END in piece:   # the model closed its message: the rest isn't the answer
                    piece, closed = ("" if closed else piece.split(END)[0]), True
                if piece:
                    text += piece
                    if stream:
                        chunk({"content": piece})
                if ev.get("done"):
                    finish = "length" if ev.get("done_reason") == "length" and not closed else "stop"
                    usage = {"prompt_tokens": ev.get("prompt_eval_count", 0),
                             "completion_tokens": ev.get("eval_count", 0)}
                    break
            usage["total_tokens"] = usage.get("prompt_tokens", 0) + usage.get("completion_tokens", 0)
            if stream:
                chunk({}, finish)
                if (req.get("stream_options") or {}).get("include_usage"):
                    chunk(None, usage=usage)
                done = b"data: [DONE]\n\n"
                self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(done), done))
                self.wfile.flush()
            else:
                out = {"id": cid, "object": "chat.completion", "created": created, "model": req["model"],
                       "choices": [{"index": 0, "finish_reason": finish,
                                    "message": {"role": "assistant", "content": text}}],
                       "usage": usage}
                self._send(200, json.dumps(out).encode(), "application/json")
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
