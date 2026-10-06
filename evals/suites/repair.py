"""Repair suite: fault-injection recovery through validate-then-repair (DP-EVAL section 5.4)."""
from __future__ import annotations

from hyperion.actions.executor import ExecutionReport
from hyperion.actions.gate import PlannedItem
from hyperion.context import new_turn
from hyperion.dsl.check import check_profile
from hyperion.dsl.render import render_profile
from hyperion.dsl.slots import extract_slots
from hyperion.ide.actions import make_action
from hyperion.issues import errors_only

from evals.faults import FAULTS, FAULT_BASE_REQUESTS, Fault
from evals.harness import EvalContext, SuiteResult, make_env
from evals.loaders import DataError


async def run(ctx: EvalContext) -> SuiteResult:
    """3 base requests x 8 faults = 24 arms; success = the final file has no errors. PASS iff 24/24 and max rounds <= 2."""
    repaired = 0
    max_rounds = 0
    failures: list[dict[str, str]] = []
    n = 0
    for request in FAULT_BASE_REQUESTS:
        text = render_profile(extract_slots(request, "native").slots)
        for fault in FAULTS:
            assert isinstance(fault, Fault)
            n += 1
            bad = fault.apply(text)
            if errors_only(check_profile(bad)):
                pass
            else:
                raise DataError(
                    f"repair {fault.name}: fault applied to {request!r} raises no checker error"
                )
            ae = make_env(ctx.cfg, files={})
            turn = new_turn(ae.env.settings, f"repair-{fault.name}", request)
            report = ExecutionReport(emitted=[], landed={}, validated={})
            item = PlannedItem(make_action("create_file", "app.yaml", bad), None)
            await ae.env.sim.run_events(
                ae.env.executor.run(
                    turn,
                    [item],
                    ["app.yaml"],
                    authorization="not_required",
                    report=report,
                )
            )
            final = ae.env.workspace.files.get("app.yaml", "")
            max_rounds = max(max_rounds, report.repair_rounds)
            if not errors_only(check_profile(final)):
                repaired += 1
            else:
                failures.append({"request": request, "fault": fault.name})
    summary = f"repaired={repaired}/{n} max_rounds={max_rounds}"
    return SuiteResult(
        name="repair",
        status="PASS" if repaired == n and max_rounds <= 2 else "FAIL",
        summary=summary,
        metrics={"repaired": repaired, "repaired_n": n, "max_rounds": max_rounds},
        details=failures,
        hard=True,
    )
