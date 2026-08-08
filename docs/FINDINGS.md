# Findings

Everything here was measured against the live servers, not inferred from documentation.
Written down because re-deriving it is expensive. Dates are 2026-08-08 unless stated.

---

## 1. The three local models

Served by three separate vLLM processes behind one ngrok tunnel.

| key | model id | route | context |
|---|---|---|---|
| `gemma` | `google/gemma-4-31B-it` | `/gemma/v1` | 65536 |
| `qwen` | `Qwen/Qwen3.6-27B` | `/qwen/v1` | 65536 |
| `gpt` | `openai/gpt-oss-20b` | `/gpt/v1` | 65536 |

All three speak the OpenAI-compatible API, so `langchain_openai.ChatOpenAI` is a drop-in.
Context was 32768 earlier in the session and was raised to 65536 on relaunch.

### Required vLLM launch flags

Tool calling returns **HTTP 400** unless the server is started with both flags. Each model
needs its own parser — they emit tool calls in different formats.

```bash
vllm serve google/gemma-4-31B-it --enable-auto-tool-choice \
  --tool-call-parser gemma4 --reasoning-parser gemma4 \
  --chat-template examples/tool_chat_template_gemma4.jinja
```

```bash
vllm serve Qwen/Qwen3.6-27B --enable-auto-tool-choice \
  --tool-call-parser hermes --reasoning-parser qwen3
```

```bash
vllm serve openai/gpt-oss-20b --enable-auto-tool-choice \
  --tool-call-parser openai --reasoning-parser openai_gptoss
```

Gemma specifics, learned the hard way:

- `pythonic` is the **wrong** parser. Gemma emits `<|tool_call>call:name{arg:<|"|>val<|"|>}<tool_call|>`,
  which `pythonic` cannot parse — `tool_calls` stays empty and the raw markup leaks into
  `content`.
- The `--chat-template` is not optional. Without it the model isn't prompted in the format the
  parser expects.
- There is an open upstream bug, [vLLM #39392](https://github.com/vllm-project/vllm/issues/39392):
  `Gemma4ToolParser` can return `<pad>`-filled responses under concurrency. **It did not
  reproduce on this build** — 48/48 clean at 8, 16 and 24 parallel tool-calling requests.

Error messages that identify a missing flag:

```
"auto" tool choice requires --enable-auto-tool-choice and --tool-call-parser to be set
tool_choice="required" requires --tool-call-parser to be set
```

gpt-oss is the dangerous one: before the flags it returned **HTTP 200 with `tool_calls: []`**
rather than erroring, so an agent appears to run and silently never calls a tool.

---

## 2. Capability matrix (measured via LangChain 1.3.14)

Identical and working on all three: `invoke`, `batch`, `stream`, `ainvoke`, `astream`,
`SystemMessage`, multi-turn history, `usage_metadata`, `response_metadata`, `get_num_tokens`,
LCEL chains, `StrOutputParser`, `JsonOutputParser`, `with_retry`, `with_fallbacks`, LangGraph
`StateGraph`, determinism at `temperature=0`, stop sequences.

After the launch flags were added, also working on all three: `bind_tools`,
`with_structured_output(method="function_calling")`, and LangGraph `create_react_agent`.

Versions used: `langchain 1.3.14`, `langchain-core 1.5.3`, `langchain-openai 1.4.2`,
`langgraph 1.2.10`, `openai 2.53.0`.

---

## 3. Thinking / reasoning behaviour

Each model behaves differently. This matters because reasoning consumes the output budget.

| | default | switch | where reasoning goes |
|---|---|---|---|
| gemma | **off** | `enable_thinking:true` turns it on | `content`, prefixed `thought`, **no closing tag** |
| qwen | **on** | `enable_thinking:false` turns it off | `content` (no reliable delimiter) |
| gpt-oss | on | **ignores the flag** | separate `reasoning` field |

```python
extra_body={"chat_template_kwargs": {"enable_thinking": False}}
```

Measured on qwen, same question: **433 output tokens → 36** with thinking off, and the answer
quality was unchanged (127 → 119 chars).

Details worth keeping:

- **Gemma's thinking mode is unusable.** With it on, the answer is concatenated directly onto
  the reasoning with no separator: `..."4 eder."2+2 = 4 eder.` There is no `</thought>`, no
  `<thought>`. Leave it off (which is the default).
- **Do not string-split qwen on `</think>`.** The tag appeared at `temperature=0.7` and was
  absent at `temperature=0.0`. Unreliable. Use the flag, or the server-side
  `--reasoning-parser`.
- **gpt-oss uses `reasoning`, not `reasoning_content`.** Measured: `content=97, reasoning=434,
  reasoning_content=0`. **LangChain drops it** — `additional_kwargs` contained only
  `['refusal']`. Raw HTTP preserves information that `ChatOpenAI` discards.
- **The server silently ignores unknown `chat_template_kwargs`.** A deliberately bogus key
  produced byte-identical output to the default, so "no error" never proves a flag is
  supported. Always verify by observing a behaviour change.

---

## 4. Structured output — use `function_calling`

LangChain's default method for `with_structured_output` is `function_calling`, and before the
launch flags it returned 400 everywhere. That is fixed. The important finding is about
**accuracy**, not availability.

Given text containing **no bank name** and a field typed `Optional[str] = None`:

| method | qwen | gpt-oss |
|---|---|---|
| `json_schema` | `"Katılım Bankacılığı (Örnek: Ziraat Katılım…)"` | `"KrediKâr"` |
| `function_calling` | **`None`** ✅ | **`None`** ✅ |

`json_schema` fabricates values for absent fields — deterministically, 5/5 runs. `qwen` invented
**"Ziraat Bankası"**, a real bank. For a banking comparison product that is a serious defect.

`json_mode` is unusable on all three: it only enforces *valid JSON*, not the schema. Each model
invented its own field names (`kampanya_adi`, nested `{kampanya:{...}}`, `campaign`).

**Conclusion: `method="function_calling"` for extraction.** Mark optional fields
`Optional[...] = None` and instruct the model to emit null rather than guess.

Two more extraction notes:

- **Field descriptions drive normalisation.** With a bare schema, `%1,89` was read as `0.0189`.
  With `Field(description="Profit rate, e.g. 1.89")` it returned `1.89` consistently, 10/10.
  Şartname 5.6 requires `%2,05` / `2.05%` / `2,05` to normalise identically, so every numeric
  field needs an explicit example in its description.
- **gpt-oss returns empty content below ~300 `max_tokens`** — reasoning eats the budget first.
  No exception, `finish_reason='length'`, `content=''`.

| max_tokens | 60 | 120 | 300 | 800 |
|---|---|---|---|---|
| content length | **0** | **0** | 153 | 153 |

---

## 5. MCP integration traps

Both of these cost hours.

### `get_tools()` breaks stateful MCP servers

`MultiServerMCPClient.get_tools()` opens a **new session per tool call**. With Playwright that
means a fresh browser each time: `browser_navigate` succeeds, then `browser_snapshot` reports
`about:blank` with an empty page, and the agent loops until it hits the recursion limit.

```python
# WRONG — new browser every call
tools = await client.get_tools()

# RIGHT — one session for the whole conversation
async with client.session("playwright") as session:
    tools = await load_mcp_tools(session)
```

Proven side by side: `get_tools()` → `Page URL: about:blank`; `client.session()` →
`Page URL: https://example.com/` with the full accessibility tree.

### Tool results are lists, and `str()` mangles them

MCP tools return `[{'type': 'text', 'text': '...'}]`. Calling `str()` on that produces a Python
**repr**, where newlines become the two-character sequence `\n`. Two consequences: the model
reads repr noise on every tool result, and any regex over the text silently matches nothing.

```python
def to_text(result) -> str:
    if isinstance(result, list):
        return "\n".join(
            str(p.get("text", p)) if isinstance(p, dict) else str(p) for p in result
        )
    return str(result)
```

---

## 6. vLLM message-shape rules

Two 400s that come from message list construction, not content:

- **`System message must be at the beginning.`** Injecting a `SystemMessage` mid-conversation
  fails. Extra guidance must be folded into the `HumanMessage` instead.
- **`Found AIMessages with tool_calls that do not have a corresponding ToolMessage.`** Any
  trimming that separates an `AIMessage.tool_calls` from its `ToolMessage` replies breaks the
  request. The rule is bidirectional — a `ToolMessage` also needs its parent present. An
  unanswered tool call in the **last** position is legal (the agent just requested it).

Any context-trimming step must run a repair pass afterwards.

---

## 7. Context sizing

- One bank homepage `browser_snapshot` is **~65,000 characters ≈ 16K tokens**.
- Estimate tokens as **`chars / 3`**, not `/4`. Measured: an estimated 40,000 was actually
  56,994 on the server (~1.4×). Turkish text and YAML accessibility trees produce more tokens
  per character than English prose.
- A full agent run on one bank task reached 120 messages / ~57K of the 65,536 window.

---

## 8. Agent Skills format

Spec: <https://agentskills.io/specification>

A skill is a **directory** containing `SKILL.md`. Only two frontmatter fields are required:

- `name` — must match the directory name, ≤64 chars, lowercase alphanumeric and hyphens, no
  leading/trailing hyphen, no consecutive hyphens.
- `description` — ≤1024 chars, says both *what it does* and *when to use it*.

Optional: `license`, `compatibility`, `metadata` (string→string map), `allowed-tools`.
Everything domain-specific belongs under `metadata`, never as invented top-level keys. The body
is freeform Markdown, loaded only once the skill activates; keep it under ~500 lines.

### What we learned building one

- **Three agents, not one.** A doer solves the task; a separate **writer** LLM analyses the
  trajectory and authors the skill; a third consumes it. Mechanical extraction of the tool calls
  does not work — it records the dead ends and picks the last `goto` as the target, which was
  the wrong page.
- **The writer needs a diagnosis stage first.** Asked only to "describe the path", it writes the
  happy path and omits the hidden steps. Asked explicitly *what errored, which URLs were dead
  ends, what non-obvious step was required (Enter? wait? click-before-type?), where were calls
  wasted* — it produces the useful content. Feed it a measured waste report (call counts, error
  counts, URLs visited, repeated calls) so the diagnosis rests on facts.
- **A skill must state that it is a route, not an answer.** Otherwise the model reads the
  example figures and reports them without touching the browser.
- **Matching must require the task's own words.** Scoring on shared generic words
  (`finansmani`, `payi`) made a *konut finansmanı* query match the *alışveriş finansmanı* skill.
  Require ~60% coverage of the task name's tokens, with stem-aware comparison so "finansman"
  still matches "finansmani".
- Skills that a human has corrected need a lock flag, or the writer overwrites them on the next
  successful run.

---

## 9. Backend endpoint discovery — the big win

Bank calculators call a backend. Driving the UI is 15× slower than calling it directly.

### Kuveyt Türk shopping-finance calculator

```
POST https://www.kuveytturk.com.tr/ck0d84?30134915811C6D92B8F34A01FCF910EE
content-type: application/json
accept: application/json
x-requested-with: XMLHttpRequest
x-bone-language: TR
referer: https://www.kuveytturk.com.tr/kendim-icin/finansmanlar/alisveris-finansmanlari/alisveris-finansmani

{"i":false,"p1":"1","p2":"100000","p3":"31","p4":"ECOMMERCE",
 "p5":"ECOMMERCE","p6":"0.00","p7":"","p8":"Alışveriş Finansmanı"}
```

`p2` = amount, `p3` = term in months (verified by calling with 50000/12 and getting
`LoanAmount: 50000, InstallmentCount: 12`).

Response: `Meta` with `InstallmentPayment`, `TotalAmount`, `ProfitRate`, `MonthlyCost`,
`YearlyCost`, `AnnualSimpleProfitRate`, `AllocationAmount`, `SurveyFee`, `HypothecFee`; plus
`Installments[]` with per-row `PrincipalAmount`, `ProfitAmount`, `BSMV`, `KKDF`,
`RemainingPrincipalAmount`, `MaturityDate`.

**Works with no browser, no cookies, no session** — a cold `httpx.post` returns in 0.25 s and
matches the site exactly (6671.82 / 206827.27).

### Cost comparison, same question

| | browser + skill | live endpoint |
|---|---|---|
| time | 133.2 s | **8.8 s** |
| tool calls | 41 | **1** |
| input tokens | ~57,000 | **1,141** |
| accuracy | matches site | matches site exactly |

The endpoint call itself is 0.27 s; the remaining 8.5 s is the LLM writing the report.

### Notes and cautions

- The hash in `ck0d84?<hash>` is **not** session-based. The page serves other hashes
  (`DDD9F3AA…`, `C79B799A…`) concurrently, and a hash captured hours earlier still returned 200.
  Persistent, but not guaranteed — extract it from the page rather than hardcoding, and verify
  by calling.
- **Don't judge a request by its URL.** `ck0d84?<hash>` looks like an Akamai bot beacon; it is
  the calculator. The only way to tell is reading the response body.
- A formula was reverse-engineered as a fallback — annuity at `profit_rate × 1.30` (KKDF 15% +
  BSMV 15%) — and reproduces the installment to 1 kuruş but drifts 61 kuruş on the total. The
  live endpoint is strictly better: exact, and rates update themselves.

### Can an agent discover this on its own?

Yes — **but only with the method in the prompt.**

| | generic prompt | method encoded |
|---|---|---|
| result | **failed** | complete, correct contract |
| time | 41.9 s | 96.9 s |
| tool calls | 19 | 35 |
| read a response body? | **never** | yes |

A 27B model will not invent "read the response body to confirm" by itself — it listed requests
four times and guessed. Once told *read the body, and don't judge by the URL because it may be
a meaningless hash*, it went straight to `browser_network_request {index, part}` and produced a
contract that worked verbatim.

Practical implication: discovery is a **fixed procedure**, not improvisation. A scheduled agent
can re-verify the contract and re-discover on drift. Note most of its 35 calls were spent
fighting the form (stale refs, values not sticking), not on the network investigation — that
part took ~6 calls.

---

## 10. Miscellaneous

- `temperature=0.0` does **not** make an agent deterministic end-to-end. Identical prompts took
  20, 41, 44 and 92 tool calls. The variance comes from the live site, not from sampling.
- Loop detection must count **consecutive** identical calls. Counting totals killed runs that
  were making real progress — `browser_snapshot {}` on four different pages is not a loop.
- Python's `urllib` fails TLS on this machine (`CERTIFICATE_VERIFY_FAILED`) under
  `/usr/local/bin/python3`. `httpx` bundles certifi and works.
- Playwright MCP exposes 24 tools, ~4.1K tokens of schema. `browser_run_code_unsafe` runs
  arbitrary JS in the page and should be excluded from any agent's tool set.
- `browser_evaluate` fails with `SyntaxError: Unexpected end of input` on multi-line or
  commented functions. Single-line expressions only.
- `browser_click` needs a `ref` from a snapshot in `target`. Passing a CSS or role string gives
  `Unexpected token while parsing css selector`.
