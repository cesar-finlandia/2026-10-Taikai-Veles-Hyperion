# Hyperion — a grounded, safe, action-taking assistant for the HyperAI IDE

Hyperion is a small web service that sits behind the chat panel of the HyperAI IDE. It answers questions about the HYPER-AI project from its documents, turns plain language into IDE actions (create, edit and delete files and folders), and checks what it writes before it tells you it is done. It was built for Veles Hack 2026, Challenge 1.

## What it does

- **Answers HYPER-AI questions** from a document index (keyword and embedding search combined), cites its sources as `[n]`, and says so when the documents do not cover a question.
- **Creates HyperAI application profiles** (native apps and device apps) from a sentence such as "create a deployment YAML for a service using the nginx Docker image". The language model only fills in a small set of fields; the file itself is rendered from a template, checked locally against the profile specification, written through the IDE, and checked again by the IDE's own validator. Anything the checks reject is repaired deterministically before you see it.
- **Asks before it changes or destroys anything that matters.** By default every edit of an existing file, every overwrite and every delete waits for a "yes" in a later message. A "no" or "cancel" makes no change at all.
- **Remembers the conversation** per `user_id`: names, files it created, and what "it" and "that file" refer to.
- **Refuses** off-topic requests and attempts to extract its instructions or keys, without refusing legitimate HyperAI questions.
- **Keeps working when parts are missing:** without an LLM key it answers from the documents and the templates; without the IDE it explains what it could not do; every stream still ends with `data: [DONE]`.

## Quick start

Run the published image (the key is read from the environment at run time and is never part of the image):

```bash
docker run --rm -p 8000:8000 --env-file .env --add-host=host.docker.internal:host-gateway libitum101/hyperion:latest
```

Without a key the service still starts and answers in document-only mode:

```bash
docker run --rm -p 8000:8000 --add-host=host.docker.internal:host-gateway libitum101/hyperion:latest
```

Talk to it directly:

```bash
curl -N -X POST http://localhost:8000/chat -H "Content-Type: application/json" \
  -d '{"user_id":"demo","text":"What is HyperAI?"}'
```

Run it together with the organizers' IDE (the agent looks for the IDE backend at `IDE_BACKEND_URL`):

```bash
docker run --rm -p 3001:3001 -e AUTH_ENABLED=false --name ide-backend donmichael/ide-backend:latest
docker run --rm -p 5000:80 --name ide-gui donmichael/ide-gui:latest
docker compose up --build        # this repository's service on :8000
```

Open http://localhost:5000/ and use the Hyperion chat panel.

From source (Python 3.14 and `uv` required):

```bash
cp .env.example .env             # then put your key into API_KEY
uv sync
uv run main.py
uv run python scripts/smoke_chat.py --base http://localhost:8000
```

## Configuration

All settings are environment variables; every one has a default and an invalid value falls back to it.

| variable | default | meaning |
|---|---|---|
| `API_KEY` | empty | key for the LLM server; empty means document-only mode |
| `LLM_BASE_URL` | `https://legion1.di.uoa.gr/v1` | any OpenAI-compatible server (a local Ollama works: `http://localhost:11434/v1`) |
| `LLM_MODEL` / `EMBED_MODEL` | `llama3.1` / `nomic-embed-text` | chat and embedding model names |
| `IDE_BACKEND_URL` | `http://localhost:3001/api` (the image sets `http://host.docker.internal:3001/api`) | where the IDE backend answers |
| `HITL_MODE` | `strict` | `strict`: edits, overwrites and deletes ask first; `destructive`: only deletes and create-over-existing ask |
| `DEBUG_ENDPOINTS` | `true` | serve `/debug/turns` and `/debug/status`; set `false` to hide them |
| `LLM_CONCURRENCY` | `3` | simultaneous model calls across all users |
| `TURN_LLM_BUDGET` / `TURN_DEADLINE_S` | `6` / `90` | model calls and seconds allowed per turn |
| `FEATURE_RAG`, `FEATURE_GUARD`, `FEATURE_MEMORY`, `FEATURE_TEMPLATES`, `FEATURE_REPAIR`, `FEATURE_HITL` | `true` | switches used by the evaluation ablations; leave them on |

Endpoints: `POST /chat` (Server-Sent Events), `GET /health`, `GET /debug/turns?user_id=&limit=`, `GET /debug/status`.

## How a turn works

```
request ─ input bounds ─ pending confirmation? ─ memory recall? ─ action request? ─ guard ─ ask
                │                │                    │                │            │      │
                │          yes/no executes or      answered from     plan → render  scope   retrieve → answer
                │          drops the saved plan    stored facts      → local check  rules   → verify citations
                │                                                     → confirm if  + one
                │                                                     required     bounded
                │                                                     → emit       model call
                │                                                     → wait for the IDE to apply it
                │                                                     → IDE validator → repair (≤ 2 rounds)
                └─ every branch ends with the same stream: text increments, action events, data: [DONE]
```

A reply is streamed as `data: {"response": "..."}` increments. IDE actions are streamed as events with an `action` field. The agent waits until the IDE has applied a write before it validates it, because the browser applies the actions.

## Measured results

Every number below is produced by `scripts/run_evals.py` and carries its mode (`offline` needs no key; `live` uses the real model). Each claim states what it does not establish.

<!-- scorecard:start -->

| claim | mode |
|---|---|
| Answer-keyword accuracy 64% (32/50) on the frozen golden set (offline). | offline |
| Top-5 source hit rate 100% (50/50) on the frozen golden set (offline). | offline |
| Guardrail macro-F1 0.914 on 110 frozen prompts; over-refusal 0.000 (45/45); off-topic leak 0.000; injection leak 0.250 (offline). | offline |
| 16/16 scripted multi-turn dialogues passed (39/39 turns), including interleaved users. | offline |
| 0 state-changing actions were emitted without a recorded confirmation across 128 checked actions in this run. | offline |
| 24 of generated profiles accepted on first write and 24 after repair (24 requests). | offline |
| Valid-profile rate: free-writing control n/a, retrieval-assisted free-writing n/a, full pipeline 100% (48 generations per arm). | offline |

<!-- scorecard:end -->

Reproduce: `uv run python scripts/run_evals.py --suite all --mode offline --gate` (no key needed) and, with a key, `uv run python scripts/run_evals.py --suite all --mode live --repeats 2 --users 8 --gate --out evals/results/live`. The scorecards are committed under `evals/results/`. The profile checker used in the evaluation is a local mirror of the IDE validator; the real validator's verdict on three generated profiles is recorded in `docs/ACCEPTANCE.md`.

## Safety model

- A new file or folder is created without asking. In `strict` mode (the default) editing an existing file, overwriting it and every delete require a confirmation from the same `user_id` in a later turn; one confirmation covers the whole batch that was shown.
- The only automatic edits are repairs of the agent's own just-written profile, and they are recorded in the trace as repairs.
- Paths are relative, contain no `..`, and use only the five IDE actions; anything else is refused before an event is emitted.
- The evaluation counts state-changing actions without a recorded confirmation; the gate requires zero.
- Keys are read from the environment, never logged, never part of a trace, never part of the image.

## Repository layout

| path | contents |
|---|---|
| `hyperion/` | the service: `agent/` (router, ask flow, turn pipeline), `actions/` (planner, confirmation gate, executor), `dsl/` (profile checker, templates, repair), `rag/`, `memory/`, `guard/`, `ide/` (gateway, path safety), `llm/` |
| `corpus/seed/` | documentation excerpts used as the base of the search index |
| `data/index.json` | the derived search index shipped in the image |
| `evals/` | frozen question, probe, dialogue and ablation sets, suites, scorecard generator |
| `scripts/` | `smoke_chat.py`, `run_evals.py`, `build_index.py`, `llm_smoke.py`, `check_hygiene.py`, `build_image.py`, `check_image.py` |
| `release/` | helpers of the release scripts |
| `tests/` | unit and integration tests |
| `docs/ACCEPTANCE.md` | the manual checklist run against the real IDE |

## Tests and evaluations

```bash
uv run pytest tests/unit -q
uv run pytest tests/integration -q
uv run python scripts/run_evals.py --suite all --mode offline --gate
uv run python scripts/check_hygiene.py --final
uv run python scripts/check_image.py --image hyperion:local
```

Tests never need a key or the network: a scripted fake model and a simulated IDE stand in for them. Tests marked `live` run only when `API_KEY` is set.

## What is new compared with the starter

The organizers' starter provided a FastAPI service with a `/chat` stub, `helpers.py`, a Dockerfile and a compose file. Everything under `hyperion/`, `evals/`, `release/`, `scripts/` (except the starter files), `tests/`, `corpus/seed/`, `data/` and `docs/` was written during the event. The starter's LLM-framework dependency was removed; the service uses plain `asyncio`, FastAPI, the `openai` client and `pydantic`.

## AI assistance disclosure

AI assistants were used to plan and to write this project under the author's direction. The details, including what the author decided and how the output was checked, are in [disclosure.md](disclosure.md). At run time the service uses only the organizers' legion1 server (Llama 3.1 8B and `nomic-embed-text`) or a local OpenAI-compatible server.

### AI use

The demo video's narration, music and background pictures were produced with AI tools. The screen recording of the product is real and unedited.

## Data and credits

The search index is derived from the organizers' HYPER-AI documentation and the HyperAI IDE tutorial pages; they remain the property of their authors and are credited here as data sources. The raw Drive documents are not redistributed in this repository. Hosting of the model: the Veles Hack organizers.

## Licence

Apache License 2.0 — see `LICENCE` (a byte-identical copy is provided as `LICENSE`).
