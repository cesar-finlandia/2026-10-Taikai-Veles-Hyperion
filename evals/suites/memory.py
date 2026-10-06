"""Memory suite: scripted multi-turn dialogues through the simulated IDE (DP-EVAL section 5.4)."""
from __future__ import annotations

from typing import Any

from evals.harness import EvalContext, SuiteResult, make_env, run_turn
from evals.loaders import load_dialogues


def _check_turn(
    rec: Any, expect: dict[str, Any], workspace: dict[str, str]
) -> str | None:
    """First failed expectation as a sentence, or None when every present key holds."""
    actions = [a.get("action", "") for a in rec.actions]
    if "actions" in expect and list(expect["actions"]) != actions:
        return f"actions {list(expect['actions'])} != {actions}"
    if "action_paths" in expect:
        paths = [a.get("path", "") for a in rec.actions]
        if list(expect["action_paths"]) != paths:
            return f"action_paths {list(expect['action_paths'])} != {paths}"
    low = rec.text.lower()
    for sub in expect.get("text_contains", []):
        if str(sub).lower() not in low:
            return f"text_contains {sub!r} missing"
    for sub in expect.get("text_not_contains", []):
        if str(sub).lower() in low:
            return f"text_not_contains {sub!r} present"
    for path, sub in expect.get("workspace_has", {}).items():
        content = workspace.get(path)
        if content is None or str(sub) not in content:
            return f"workspace_has {path!r} missing {sub!r}"
    for path in expect.get("workspace_missing", []):
        if path in workspace:
            return f"workspace_missing {path!r} still present"
    return None


async def run_dialogues(
    ctx: EvalContext, *, control: bool = False
) -> tuple[int, int, int, int, list[dict[str, Any]]]:
    """Run every dialogue; return (dialogues_ok, n_dialogues, turns_ok, n_turns, failures).

    Control: the user id of turn `i` is replaced by `f"{user}#{i}"` (every turn arrives as a fresh session).
    """
    dialogues = load_dialogues(ctx.cfg.data_dir)
    d_ok = 0
    t_ok = 0
    t_n = 0
    failures: list[dict[str, Any]] = []
    for dialogue in dialogues:
        ae = make_env(ctx.cfg, files=dict(dialogue.workspace))
        failed: str | None = None
        for i, turn in enumerate(dialogue.turns):
            user = f"{turn.user}#{i}" if control else turn.user
            rec = await run_turn(ctx, ae, user, turn.say)
            t_n += 1
            problem = _check_turn(rec, turn.expect, ae.env.workspace.files)
            if problem is None:
                t_ok += 1
            elif failed is None:
                failed = f"{dialogue.id} turn {i + 1}: {problem}"
        if failed is None:
            d_ok += 1
        else:
            failures.append({"dialogue": dialogue.id, "error": failed})
    return (d_ok, len(dialogues), t_ok, t_n, failures)


async def run(ctx: EvalContext) -> SuiteResult:
    """Dialogues pass when all their turns pass; PASS iff every dialogue passes."""
    d_ok, n, t_ok, t_n, failures = await run_dialogues(ctx)
    summary = f"dialogues={d_ok}/{n} turns={t_ok}/{t_n}"
    return SuiteResult(
        name="memory",
        status="PASS" if d_ok == n else "FAIL",
        summary=summary,
        metrics={
            "dialogues": d_ok,
            "dialogues_n": n,
            "turns": t_ok,
            "turns_n": t_n,
        },
        details=failures,
        hard=True,
    )
