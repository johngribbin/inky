# Benchmark results

**Hardware:** Apple M4 Max, 128 GB unified memory, macOS 26.5.2, Low Power Mode off.
**Date:** 2026-11-25.

**Prompt:** "Write a Python function that parses a CSV of transactions and returns totals per category, with tests"
Temperature 1.0 (official generation_config), max_tokens 8192. The long test prepends about 13.6k tokens of code context to approximate an agent-sized prompt. All timings are client-side wall clock from the streamed response, with 2 runs per row (range shown).

| Setup | Effort | TTFT (short prompt) | Gen tok/s | Tokens out | Total time | Prompt tok/s (13.6k) | TTFT (13.6k) |
|---|---|---|---|---|---|---|---|
| llama.cpp b12588 (Unsloth build, Metal), UD-Q4_K_XL GGUF + DSpark Q8_0 draft, ctx 65k, `-ngl 99` | high (template default) | 0.15–0.7 s | 34–39 | 3150–3981 | 81–106 s | 275 | 50 s |
| llama.cpp (same) | low | 0.15–0.5 s | 38–42 | 1086–1131 | 27–29 s | — | — |
| Ollama 0.40.17 MLX runner, official NVFP4/MXFP8 + DSpark, ctx 32k | low | 0.16–0.34 s | 55–60 | 867–876 | 15–16 s | 435–510 | 27–31 s |
| Ollama (same) | high | 0.15–0.35 s | 47–51 | 3189–3862 | 68–76 s | — | — |

Prompt tok/s on the 35-token short prompt is too noisy to mean anything. The 13.6k column is the one that matters for coding agents.

**Takeaways**
- The engine alone gives about 40–50% faster generation and about 1.7x faster prompt processing.
- Low effort instead of high produces about 4x fewer tokens on this task, with a complete answer and tests in both cases.
- Together, the same task went from about 90 s to about 15 s, roughly 6x faster end to end.
