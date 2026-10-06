"""Agent test environment: act Env plus guard, retriever, ask flow and Agent (DP-AGENT-CORE §3)."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from hyperion.agent.ask import AskFlow
from hyperion.agent.locks import UserLocks
from hyperion.agent.pipeline import Agent
from hyperion.config import Settings
from hyperion.guard.engine import Guard
from hyperion.llm.base import LLMLike
from hyperion.rag.chunker import chunk_document
from hyperion.rag.index import RagIndex
from hyperion.rag.ingest import load_corpus
from hyperion.rag.retriever import Retriever
from hyperion.testing.actions_env import Env, make_env
from hyperion.testing.ide_simulator import SimulatedTurn
from hyperion.trace import TraceStore


@dataclass
class AgentEnv:
    env: Env  # hyperion.testing.actions_env.Env (settings, llm, backend, workspace, gateway, store, flow, sim ...)
    agent: Agent
    ask: AskFlow
    guard: Guard
    retriever: Retriever | None
    traces: TraceStore


@lru_cache(maxsize=1)
def _seed_index() -> RagIndex:
    chunks = [chunk for doc in load_corpus(Path("corpus/seed")) for chunk in chunk_document(doc)]
    return RagIndex.build(chunks, None, None)


def make_agent_env(
    files: dict[str, str] | None = None,
    *,
    llm: LLMLike | None = None,
    with_index: bool = True,
    retriever: Retriever | None = None,
    hitl_mode: str = "strict",
    base: Settings | None = None,
    **settings_kw: Any,
) -> AgentEnv:
    """env = make_env(files, llm=llm, hitl_mode=hitl_mode, base=base, **settings_kw); retriever = the given `retriever` when not None, else
    Retriever(_seed_index(), env.llm, env.settings) when with_index, else None, where _seed_index() is an lru_cache'd
    RagIndex.build(chunks of load_corpus(Path('corpus/seed')), None, None);
    guard = Guard(env.settings, env.llm, retriever); ask = AskFlow(env.settings, env.llm, env.store, retriever);
    agent = Agent(env.settings, llm=env.llm, gateway=env.gateway, store=env.store, guard=guard, flow=env.flow, ask=ask,
                  traces=TraceStore(), locks=UserLocks())."""
    env = make_env(files, llm=llm, hitl_mode=hitl_mode, base=base, **settings_kw)
    if retriever is None and with_index:
        retriever = Retriever(_seed_index(), env.llm, env.settings)
    guard = Guard(env.settings, env.llm, retriever)
    ask = AskFlow(env.settings, env.llm, env.store, retriever)
    # The agent's flow must be the env's flow, sharing its planner and executor,
    # so tests can observe or patch the flow's planner through the env.
    env.flow._planner = env.planner  # noqa: SLF001 (test wiring)
    env.flow._executor = env.executor  # noqa: SLF001 (test wiring)
    traces = TraceStore()
    agent = Agent(
        env.settings,
        llm=env.llm,
        gateway=env.gateway,
        store=env.store,
        guard=guard,
        flow=env.flow,
        ask=ask,
        traces=traces,
        locks=UserLocks(),
    )
    agent._retriever = retriever  # noqa: SLF001 (same package wiring)
    return AgentEnv(env=env, agent=agent, ask=ask, guard=guard, retriever=retriever, traces=traces)


async def say(ae: AgentEnv, user_id: str, text: str) -> SimulatedTurn:
    """await ae.env.sim.run_events(ae.agent.handle(user_id, text))   (the simulator applies emitted actions to the fake workspace)."""
    return await ae.env.sim.run_events(ae.agent.handle(user_id, text))
