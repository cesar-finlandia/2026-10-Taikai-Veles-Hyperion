"""Model prompts for the act pipeline (DP-ACTIONS §5.18)."""
from __future__ import annotations

from typing import Sequence

from hyperion.issues import Issue
from hyperion.llm.base import Message
from hyperion.memory.budget import clip

SLOTS_NATIVE_SYSTEM = (
    "You fill a small JSON object describing a HyperAI application. Output ONLY a JSON object, no prose.\n"
    "Allowed keys: name, description, image, tag, port, owner, lifecycle, cpu, memory, storage, domain, intent.\n"
    "Only include a key when the request states or clearly implies it. Never invent values.\n"
    "name: lower-case letters, digits and '-' (max 40). image: Docker image name without the tag. tag: the image tag.\n"
    "port: an integer 1-65535. lifecycle: development, testing or production. cpu like \"500m\". memory and storage like \"512Mi\" or \"1Gi\".\n"
    "Example request: web server with nginx on port 8080 for production\n"
    "Example output: {\"image\":\"nginx\",\"port\":8080,\"lifecycle\":\"production\"}"
)
SLOTS_DEVICE_SYSTEM = (
    "You fill a small JSON object describing a HyperAI device application. Output ONLY a JSON object, no prose.\n"
    "Allowed keys: name, description, image, port, owner, lifecycle, workload_kind, package_name, apk_url, chip, domain, intent.\n"
    "Only include a key when the request states or clearly implies it. Never invent values.\n"
    "workload_kind: DockerImage, AndroidApk or esp32Binary. image: Docker image with optional tag. port: an integer 1-65535.\n"
    "lifecycle: development, testing or production.\n"
    "Example request: run grafana on my raspberry pi\n"
    "Example output: {\"image\":\"grafana/grafana\",\"workload_kind\":\"DockerImage\",\"port\":3000}"
)
PATCH_SYSTEM = (
    "You edit a HyperAI application profile (YAML) by listing small operations. Output ONLY a JSON object {\"ops\": [...]}.\n"
    "Allowed operations (at most 4):\n"
    "{\"op\":\"set\",\"path\":\"<dotted.path with [index]>\",\"value\":<string|number|boolean|list|object>}\n"
    "{\"op\":\"delete\",\"path\":\"<dotted.path>\"}\n"
    "{\"op\":\"replace\",\"find\":\"<exact text that occurs once in the file>\",\"with\":\"<new text>\"}\n"
    "Prefer \"set\" with the full dotted path from the file's root, for example applicationProfile.specs.network.ports[0].port.\n"
    "Never rewrite the whole file. If the request cannot be done with these operations output {\"ops\": []}."
)
REPAIR_SYSTEM = (
    "A HyperAI application profile (YAML) failed validation. Fix ONLY the listed problems by listing small operations.\n"
    "Output ONLY a JSON object {\"ops\": [...]} with at most 4 operations, each one of:\n"
    "{\"op\":\"set\",\"path\":\"<dotted.path with [index]>\",\"value\":<string|number|boolean|list|object>}\n"
    "{\"op\":\"delete\",\"path\":\"<dotted.path>\"}\n"
    "{\"op\":\"replace\",\"find\":\"<exact text that occurs once in the file>\",\"with\":\"<new text>\"}\n"
    "Use the full dotted path from the file's root. If you cannot fix a problem output {\"ops\": []}."
)
EXPLAIN_SYSTEM = (
    "You explain a HyperAI application profile to a user in plain English.\n"
    "Use ONLY the file content and the facts given. Do not invent fields or values.\n"
    "Write 3 to 6 short sentences or dash bullets: what the application is, its image, its ports, its resources, and any problem listed."
)


def slots_messages(kind: str, text: str) -> list[Message]:
    system = SLOTS_DEVICE_SYSTEM if kind == "device" else SLOTS_NATIVE_SYSTEM
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": f"Request: {clip(text, 600)}"},
    ]


def patch_messages(path: str, content: str, request: str) -> list[Message]:
    return [
        {"role": "system", "content": PATCH_SYSTEM},
        {"role": "user", "content": f"File ({path}):\n```yaml\n{content}\n```\nRequest: {clip(request, 600)}"},
    ]


def repair_messages(path: str, content: str, issues: Sequence[Issue]) -> list[Message]:
    probs = "\n".join("- " + i.format() for i in issues[:8])
    return [
        {"role": "system", "content": REPAIR_SYSTEM},
        {"role": "user", "content": f"File ({path}):\n```yaml\n{content}\n```\nProblems:\n" + probs},
    ]


def explain_messages(path: str, content: str, facts: str, issues: Sequence[Issue]) -> list[Message]:
    probs = "; ".join(i.format() for i in issues) or "none"
    return [
        {"role": "system", "content": EXPLAIN_SYSTEM},
        {"role": "user", "content": f"File ({path}):\n```yaml\n{content}\n```\nFacts: {facts}\nProblems: " + probs},
    ]
