"""End-to-end: a local OpenAI-compatible endpoint (LM Studio, Unsloth, Ollama, llama.cpp) answers.

Skipped unless KIT_LLM_BASE_URL (default http://localhost:1234/v1) responds to GET /models.
  KIT_LLM_MODEL=<id>     pick a model explicitly (default: first listed)
  KIT_E2E_TOOLS=1        also check that the model emits an OpenAI-style tool call

What these prove is that the *endpoint* works: it completes inside the kit's token budget and
answers from the note it was given. They deliberately do not grade a 2B model's wording — an
assertion on one phrasing fails on a correct answer, which is a broken test, not a broken model.
"""
import json
import os
import subprocess
import sys
import unittest
import urllib.request

import kit
import kitproviders
from tests.helpers import ROOT, VAULT, env_flag, llm_base_url, llm_reachable

MODELS = llm_reachable()


def chat(payload: dict, timeout: int = 180) -> dict:
    """POST a completion through the kit's own opener, so a proxy env var cannot redirect it."""
    req = urllib.request.Request(f"{llm_base_url()}/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.environ.get('KIT_LLM_API_KEY', 'local')}"})
    with kitproviders.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def budget(answer_tokens: int) -> int:
    """The budget the kit would send for `answer_tokens` of answer — answer plus thinking headroom."""
    return answer_tokens + kitproviders.reasoning_headroom()


@unittest.skipUnless(MODELS, f"no OpenAI-compatible endpoint at {llm_base_url()} (start LM Studio's server or set KIT_LLM_BASE_URL)")
class LocalLlmEndToEnd(unittest.TestCase):
    model = os.environ.get("KIT_LLM_MODEL") or (MODELS[0] if MODELS else "")

    def test_models_listed(self):
        self.assertTrue(MODELS)

    def test_completion_follows_instruction(self):
        """Proves the endpoint completes a one-line instruction inside the kit's own budget.

        A reasoning model draws its chain of thought from the same max_tokens pool as the answer,
        so the budget here is the kit's: answer plus headroom. The answer is graded the way
        `llm smoke` grades it — normalised — because 'KIT' for 'KIT-OK' is the endpoint working.
        """
        b = budget(kit.SMOKE_ANSWER_TOKENS)
        data = chat({"model": self.model, "max_tokens": b, "temperature": 0,
                     "messages": [{"role": "user", "content": f"Reply with exactly the text {kit.SMOKE_TOKEN} and nothing else."}]})
        choice = data["choices"][0]
        self.assertNotEqual(choice["finish_reason"], "length",
                            f"the whole {b}-token budget went before an answer appeared: {choice}")
        self.assertTrue(kit._smoke_passed(choice["message"]["content"]), choice["message"])

    def test_grounded_answer_from_vault_note(self):
        """Proves the model answers *from the note*, not from memory and not from its own thinking.

        The bar is grounding, not wording: the decision supports 'qmd', 'option A' and 'local
        hybrid search' equally, and a 2B model picks whichever it likes. What a correct answer
        cannot do is come back empty or truncated mid-sentence.
        """
        note = (VAULT / "06-decisions/2026-09-08-adopt-qmd-for-local-search.md").read_text(encoding="utf-8")
        b = budget(200)
        data = chat({"model": self.model, "max_tokens": b, "temperature": 0,
                     "messages": [{"role": "system", "content": "Answer only from the provided note. One sentence."},
                                  {"role": "user", "content": f"Note:\n{note}\n\nQuestion: which option was chosen, A or B, and what is it?"}]})
        choice = data["choices"][0]
        text = choice["message"]["content"].strip().lower()
        self.assertEqual(choice["finish_reason"], "stop",
                         f"the answer was cut off at {b} tokens: {choice}")
        self.assertTrue(text, f"only reasoning came back, no answer: {choice['message']}")
        self.assertTrue(any(t in text for t in ("qmd", "option a", "local hybrid", "local search")),
                        f"nothing in the answer comes from the note: {text}")

    def test_kit_llm_smoke_command(self):
        r = subprocess.run([sys.executable, str(ROOT / "kit.py"), "llm", "smoke", "--model", self.model], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    @unittest.skipUnless(env_flag("KIT_E2E_TOOLS"), "set KIT_E2E_TOOLS=1 to test tool calling")
    def test_tool_call_emitted(self):
        tools = [{"type": "function", "function": {"name": "vault_search", "description": "Search the user's notes",
                  "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}]
        data = chat({"model": self.model, "max_tokens": budget(200), "temperature": 0, "tools": tools, "tool_choice": "auto",
                     "messages": [{"role": "user", "content": "Search my notes for the search relaunch latency risk."}]})
        msg = data["choices"][0]["message"]
        self.assertTrue(msg.get("tool_calls"), f"expected a tool call, got: {msg}")
        self.assertEqual(msg["tool_calls"][0]["function"]["name"], "vault_search")

    @unittest.skipUnless(env_flag("KIT_E2E_TOOLS"), "set KIT_E2E_TOOLS=1 to test tool calling")
    def test_the_model_drives_the_real_bridge(self):
        """The seam no other test crosses: the bridge's own tool schemas, as `list_tools` serves
        them over stdio, handed to the model; the calls it emits run against the bridge; the
        results go back; the model answers.

        Asserted: the model called at least one bridge tool, the bridge served a note path back,
        and the model then answered in text. Not asserted: its wording or which tool it picked.
        """
        import importlib.metadata
        try:
            if importlib.metadata.version("mcp").split(".")[0] != "2":
                self.skipTest("the bridge targets the mcp 2.x SDK")
        except importlib.metadata.PackageNotFoundError:
            self.skipTest("mcp python sdk not installed")
        import asyncio
        import shutil

        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters
        from tests.helpers import temp_vault

        vault = temp_vault()
        self.addCleanup(shutil.rmtree, vault.parent, True)
        trace: list[tuple[str, bool, str]] = []

        async def go() -> str:
            params = StdioServerParameters(command=sys.executable, env=dict(os.environ), args=[
                str(ROOT / "mcp/vault_bridge.py"), "--backend", "fs", "--vault", str(vault), "--read-only", "--no-audit"])
            async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
                await s.initialize()
                tools = [{"type": "function", "function": {"name": t.name, "description": t.description or "",
                                                           "parameters": t.input_schema}} for t in (await s.list_tools()).tools]
                messages = [
                    {"role": "system", "content": "You answer questions about the user's notes. Use the tools to look things "
                                                  "up before answering, then answer in one or two sentences and cite the note path."},
                    {"role": "user", "content": "Why did we adopt qmd for local search? Look it up in my notes."}]
                for _ in range(4):
                    msg = chat({"model": self.model, "max_tokens": budget(400), "temperature": 0, "tools": tools,
                                "tool_choice": "auto", "messages": messages})["choices"][0]["message"]
                    calls = msg.get("tool_calls") or []
                    if not calls:
                        return (msg.get("content") or "").strip()
                    messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": calls})
                    for call in calls:
                        fn = call["function"]
                        res = await s.call_tool(fn["name"], json.loads(fn.get("arguments") or "{}"))
                        text = "".join(getattr(c, "text", "") for c in res.content)
                        trace.append((fn["name"], bool(res.is_error), text))
                        messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": text})
                return ""

        answer = asyncio.run(go())
        self.assertTrue(trace, "the model never called a bridge tool")
        self.assertTrue(any(not err and ".md" in text for _, err, text in trace),
                        f"no bridge call returned a note path: {[(n, e, t[:120]) for n, e, t in trace]}")
        self.assertTrue(answer, f"no final answer after {len(trace)} tool call(s): {[n for n, _, _ in trace]}")
