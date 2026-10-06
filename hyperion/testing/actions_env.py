"""Test environment for the act pipeline (DP-ACTIONS §3)."""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any

from hyperion.actions.executor import Executor
from hyperion.actions.flow import ActionsFlow
from hyperion.actions.planner import ActionPlanner
from hyperion.config import Settings
from hyperion.context import TurnContext, new_turn
from hyperion.dsl.report import report_for
from hyperion.ide.gateway import IdeGateway
from hyperion.llm.base import LLMLike
from hyperion.llm.fake import FakeLLM
from hyperion.memory.store import SessionStore
from hyperion.testing.fake_ide import FakeIdeBackend, FakeWorkspace, Validator
from hyperion.testing.ide_simulator import IdeSimulator


@dataclass
class Env:
    settings: Settings
    llm: LLMLike
    backend: FakeIdeBackend
    workspace: FakeWorkspace
    gateway: IdeGateway
    store: SessionStore
    planner: ActionPlanner
    executor: Executor
    flow: ActionsFlow
    sim: IdeSimulator


def make_env(
    files: dict[str, str] | None = None,
    *,
    llm: LLMLike | None = None,
    hitl_mode: str = "strict",
    apply_delay_s: float = 0.02,
    validator: Validator | None = None,
    base: Settings | None = None,
    **settings_kw: Any,
) -> Env:
    settings = dataclasses.replace(
        base or Settings(api_key="test-key", landing_timeout_s=0.5, landing_interval_s=0.05),
        hitl_mode=hitl_mode,  # type: ignore[arg-type]
        **settings_kw,
    )
    workspace = FakeWorkspace(files)
    backend = FakeIdeBackend(workspace, validator=validator or report_for)
    gateway = IdeGateway(settings, transport=backend.transport())
    store = SessionStore(settings)
    llm = llm if llm is not None else FakeLLM(down=True)
    planner = ActionPlanner(settings, llm, gateway, store)
    executor = Executor(settings, llm, gateway, store)
    flow = ActionsFlow(settings, llm, gateway, store)
    sim = IdeSimulator(workspace, apply_delay_s=apply_delay_s)
    return Env(
        settings=settings,
        llm=llm,
        backend=backend,
        workspace=workspace,
        gateway=gateway,
        store=store,
        planner=planner,
        executor=executor,
        flow=flow,
        sim=sim,
    )


def new_ctx(env: Env, text: str, user_id: str = "u1") -> TurnContext:
    return new_turn(env.settings, user_id, text)
