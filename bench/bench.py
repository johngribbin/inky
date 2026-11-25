#!/usr/bin/env python3
"""Benchmark an OpenAI-compatible endpoint (llama-server or Ollama) with a fixed coding prompt.

Usage: python3 bench/bench.py BASE_URL MODEL EFFORT [--label NAME] [--max-tokens N] [--runs N] [--long]
  EFFORT: none|minimal|low|medium|high|xhigh|max|default
  --long: prepend ~8k tokens of context to measure prompt processing on agent-sized prompts.

Timing is measured client-side from streaming (wall clock):
  TTFT      = request sent -> first token (thinking or content)
  prompt/s  = prompt_tokens / TTFT
  gen/s     = (completion_tokens - 1) / (last token time - first token time)
"""
import argparse, json, time, urllib.request

PROMPT = ("Write a Python function that parses a CSV of transactions and returns "
          "totals per category, with tests")

def filler():
    # deterministic ~8k-token prefix that looks like code context an agent would send
    lines = []
    for i in range(400):
        lines.append(f"def helper_{i}(x, y):\n    \"\"\"Helper number {i}.\"\"\"\n    return (x * {i} + y) % 97\n")
    return "Here is some existing project code for context:\n```python\n" + "\n".join(lines) + "```\n\n"

def run(base, model, effort, max_tokens, long):
    content = (filler() if long else "") + PROMPT
    body = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": 1.0,
    }
    if effort != "default":
        body["reasoning_effort"] = effort                          # Ollama / OpenAI style
        body["chat_template_kwargs"] = {"reasoning_effort": effort}  # llama-server jinja
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter(); t_first = t_last = None
    usage = None; n_chunks = 0; reasoning_chars = content_chars = 0; finish = None; timings = None
    with urllib.request.urlopen(req, timeout=1800) as r:
        for raw in r:
            line = raw.decode().strip()
            if not line.startswith("data:"): continue
            data = line[5:].strip()
            if data == "[DONE]": break
            ev = json.loads(data)
            if ev.get("usage"): usage = ev["usage"]
            if ev.get("timings"): timings = ev["timings"]
            for ch in ev.get("choices", []):
                d = ch.get("delta", {})
                txt = (d.get("reasoning_content") or d.get("reasoning") or "")
                ctt = d.get("content") or ""
                if txt or ctt:
                    now = time.perf_counter()
                    if t_first is None: t_first = now
                    t_last = now; n_chunks += 1
                    reasoning_chars += len(txt); content_chars += len(ctt)
                if ch.get("finish_reason"): finish = ch["finish_reason"]
    t_end = time.perf_counter()
    pt = usage.get("prompt_tokens") if usage else None
    ct = usage.get("completion_tokens") if usage else n_chunks
    ttft = t_first - t0
    gen = (ct - 1) / (t_last - t_first) if ct and t_last > t_first else float("nan")
    return dict(ttft=ttft, prompt_tokens=pt, pp=(pt / ttft if pt else float("nan")), gen=gen,
                completion_tokens=ct, total=t_end - t0, finish=finish,
                reasoning_chars=reasoning_chars, content_chars=content_chars, timings=timings)

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("base"); ap.add_argument("model"); ap.add_argument("effort")
    ap.add_argument("--label", default="")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--long", action="store_true")
    a = ap.parse_args()
    for i in range(a.runs):
        r = run(a.base, a.model, a.effort, a.max_tokens, a.long)
        r.update(label=a.label, effort=a.effort, long=a.long, run=i + 1)
        print(json.dumps(r), flush=True)
