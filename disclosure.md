# AI assistance disclosure

**Project:** Hyperion, Veles Hack 2026, Challenge 1 (solo entry).

**Tools used**
- Claude (Anthropic): produced the written design plans and the architecture from the organizers' challenge documents, and answered design questions.
- Muse Spark 1.3 via Opencode for implementing the design plans. Wrote the source code, tests and evaluation scripts from those plans.

**What the author did**
- Chose the track, set the requirements and constraints, wrote the prompts, reviewed the plans, ran every command, made the operator decisions listed in the README, ran the real IDE acceptance checklist.

**How the output was checked**
- Automated tests with a scripted fake model and a simulated IDE; the evaluation suites in `evals/`; the hygiene gate `scripts/check_hygiene.py --final`; and a manual run against the organizers' real IDE recorded in `docs/ACCEPTANCE.md`.

**What the running service uses**
- Only the organizers' legion1 model server or a local OpenAI-compatible server. No other AI service is called.

**Fresh code**
- The organizers' starter was the starting point. All other code, data derivations and documents were written during the event; third-party libraries are used as dependencies and are listed in `pyproject.toml`.
