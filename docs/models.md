# Models — the 2B class, what runs where, and the models qmd uses

## Chat model: the 2026 2B class

Verified against public model cards and Artificial Analysis as of September 2026. "2B" means roughly 2–3 B parameters, dense, ~1.5–2 GB at Q4 — runs on any 8 GB laptop and most phones.

| Model | Maker | Params / ctx | Licence | Why it is on the list |
|---|---|---|---|---|
| **MiniCPM5-2B** (kit default) | OpenBMB / ModelBest | 2.5 B dense, 128k | Apache-2.0 | Released Sept 2026 as "2B-class SOTA"; standard Llama architecture (works in every runtime); tool calling and a thinking toggle; official GGUF at `openbmb/MiniCPM5-2B-GGUF`. Artificial Analysis: highest Intelligence Index of any open model under 4B, token-efficient, low hallucination rate because it declines what it does not know |
| Qwen3.5-2B | Alibaba | ~2 B | Apache-2.0 | The benchmark peer in the MiniCPM5 model card; broad language coverage incl. German; strong tool use |
| Gemma 4 E2B | Google | 2 B effective | Gemma terms | Google's on-device line; excellent multilingual; check the licence for commercial redistribution |
| LFM2.5-2.6B | Liquid AI | 2.6 B | LFM open licence | Hybrid architecture tuned for CPU/edge latency |
| Granite 4.2 3B | IBM | 3 B | Apache-2.0 | Enterprise-friendly licence and documentation; conservative, factual style |
| Ministral 3 (3B) | Mistral | 3 B | Apache-2.0 | The EU-origin option in this size; verify the current release name and reasoning variant before standardising on it |

How to choose: MiniCPM5-2B for a coding-agent / tool-use flavour; Qwen3.5-2B or Gemma 4 E2B when most notes are German; Granite or Ministral when licence provenance matters to a procurement review. Keep two installed and compare with the same prompt — `lms get` makes that a one-liner.

Quantisation: **Q4_K_M** is the default trade-off (≈1.6 GB for 2.5 B); **Q8_0** (≈2.7 GB) is noticeably better on instruction following if RAM allows; below Q4, 2B models degrade quickly. On Apple silicon prefer the MLX build when LM Studio offers one.

Context: these models advertise 32k–128k, but on CPU a 2B model past 16k tokens is slow and tends to lose the thread; keep qmd's `-n` at 5–10 hits and let the vault's `description` fields carry the summary.

### The default is a reasoning model, and thinking costs tokens

Measured 2026-09-15 against LM Studio serving MiniCPM5-2B, this kit's default. MiniCPM5-2B reasons before it answers, and an OpenAI-compatible server draws that chain of thought from the same `max_tokens` pool as the answer:

| `max_tokens` | reasoning tokens | answer | `finish_reason` |
|---|---|---|---|
| 20 | 16–20 | empty, or a truncated `KIT` | `length` |
| 512 | 16–32 | `KIT-OK` | `stop` |

So a budget sized for the answer alone buys nothing but thinking. The kit therefore adds `kitproviders.REASONING_HEADROOM` (512 tokens, `KIT_LLM_REASONING_TOKENS` to change) to every request on top of the budget the caller asked for. On a model that does not reason this is free: `max_tokens` is a ceiling, not a target. The chain of thought comes back in a separate field (`reasoning_content` in LM Studio, `reasoning` elsewhere) and the kit never prints it as an answer.

Two knobs if a 2B model still comes back empty: raise `KIT_LLM_REASONING_TOKENS`, or turn the model's thinking toggle off in the app — for retrieval-grounded summarising it buys little. Re-check when the default model changes.

## Embedding models for qmd

qmd downloads three GGUF models on first use (`~/.cache/qmd/models/`):

| Role | Default | Size | Note |
|---|---|---|---|
| embeddings | `embeddinggemma-300M-Q8_0` | ~300 MB | English-optimised |
| re-ranking | `qwen3-reranker-0.6b-q8_0` | ~640 MB | used by `qmd query` |
| query expansion | `qmd-query-expansion-1.7B-q4_k_m` (fine-tuned Qwen3-1.7B) | ~1.1 GB | used by `qmd query` |

**German or mixed-language vaults**: switch embeddings to Qwen3-Embedding (119 languages) *before* the first `embed`, or re-embed with `-f` afterwards — vectors are not compatible across models:

```bash
export QMD_EMBED_MODEL="hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf"
qmd embed -f
```

Or set `models.embed:` in `~/.config/qmd/index.yml`. CPU-only machines: `qmd query --no-rerank` skips the most expensive step; `QMD_FORCE_CPU=1` avoids flaky GPU probes.

## Hardware reference

| Machine | Chat model | qmd | Notes |
|---|---|---|---|
| 8 GB laptop, no GPU | 2B @ Q4, 8k ctx | fine; `--no-rerank` for speed | expect 8–15 tok/s |
| 16 GB Apple silicon | 2B @ Q8 or MLX; 4B fits | full pipeline | Metal accelerates both |
| Windows + NVIDIA | 2B–8B | CUDA; set `QMD_EMBED_PARALLELISM=1` if embedding crashes | see [platforms/windows.md](platforms/windows.md) |
| Homelab (Ollama on k3s, RK1/NUC) | 2B–4B served over LAN | run qmd where the vault is | point `KIT_LLM_BASE_URL` at the service |
