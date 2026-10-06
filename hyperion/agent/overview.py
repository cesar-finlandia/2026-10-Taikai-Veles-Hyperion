"""Built-in documentation overview used when the RAG index is missing (DP-AGENT-CORE §5.5)."""
from __future__ import annotations

from hyperion.rag.text import content_terms
from hyperion.rag.types import Chunk, Hit

_BODIES: tuple[tuple[str, str], ...] = (
    (
        "HYPER-AI project",
        "HYPER-AI is an EU-funded research project (Grant Agreement 101135982) that develops "
        "self-abstracted and self-coordinated cloud-to-edge resources, joining IoT, Edge and Cloud computing "
        "into one computing continuum. CERTH coordinates the project; partners include Telefónica, "
        "the Eclipse Foundation, the National and Kapodistrian University of Athens, "
        "the Cyprus University of Technology, eBOS, ENEA and CSEM.",
    ),
    (
        "HyperAI IDE",
        "The HyperAI IDE is the development environment of HyperAI. Users describe applications as YAML "
        "application profiles, validate them live and deploy them from the IDE. Hyperion is the LLM-powered "
        "assistant inside the IDE: it answers questions about HYPER-AI and creates, edits and deletes files "
        "on the user's behalf.",
    ),
    (
        "Native apps",
        "A native app profile has the root key applicationProfile with metadata, specs and an optional status. "
        "The specs contain runtime (containerImage with uri and tag, entryPoint, args), resources (cpu, memory, "
        "storage), network (ports, protocols), constraints and qos. Native apps run as containers or virtual "
        "machines on swarm nodes.",
    ),
    (
        "Device apps",
        "A device app profile uses apiVersion hyper.ai/v1 and kind Application with metadata and spec. "
        "The spec has app, workload, exec, resources, network, qos and constraints. "
        "The workload kind is DockerImage, AndroidApk or esp32Binary. Device apps target registered devices "
        "such as Android phones, ESP32 boards and Docker-capable edge devices.",
    ),
    (
        "Use cases",
        "HYPER-AI addresses five verticals: Mobility and Automotive, Industry 4.0, Healthcare, Green Energy, "
        "and Farming and Agriculture.",
    ),
    (
        "What Hyperion can do",
        "Hyperion answers questions from the HYPER-AI documentation, creates application profiles such as "
        "a deployment YAML for an nginx container, validates files with the IDE validator, repairs problems, "
        "and edits or deletes files after asking the user to confirm.",
    ),
)

BUILTIN_CHUNKS: tuple[Chunk, ...] = tuple(
    Chunk(
        id=f"builtin#{i:03d}",
        doc_id="builtin-overview",
        title=title,
        section="",
        text=f"{title}\n{body}",
        source="built-in overview",
        url=None,
    )
    for i, (title, body) in enumerate(_BODIES)
)


def select_builtin_hits(question: str, *, k: int = 3) -> list[Hit]:
    """Rank BUILTIN_CHUNKS by distinct content-term overlap with the question. See §5.5."""
    try:
        q = set(content_terms(question))
    except Exception:
        return []
    if not q:
        return []
    threshold = 1 if len(q) <= 2 else 2
    scored: list[tuple[int, int, Chunk]] = []
    for index, chunk in enumerate(BUILTIN_CHUNKS):
        try:
            score = len(q & set(content_terms(chunk.text)))
        except Exception:
            score = 0
        if score >= threshold:
            scored.append((score, index, chunk))
    scored.sort(key=lambda item: (-item[0], item[1]))
    out: list[Hit] = []
    for rank, (score, _index, chunk) in enumerate(scored[:k], start=1):
        out.append(Hit(chunk=chunk, score=float(score), bm25=float(score), dense=None, rank=rank))
    return out
