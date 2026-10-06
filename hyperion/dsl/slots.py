"""Deterministic slot extraction from a user request (no model calls)."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from hyperion.dsl.detect import DEVICE_CUES, detect_kind_from_request
from hyperion.dsl.spec import PATTERNS, ProfileKind


@dataclass
class PortSlot:
    port: int
    protocol: str = "TCP"            # native default 'TCP'; device default 'HTTP'
    public: bool = True


@dataclass
class NativeSlots:
    name: str = "my-app"
    version: str = "1.0.0"
    description: str = ""
    owner: str = "HyperAI-User"
    lifecycle_phase: str = "development"
    intent: str = "service-deployment"
    domain: str = "web-service"
    image_uri: str = "nginx"
    image_tag: str = "latest"
    entry_point: str = "nginx"
    args: list[str] = field(default_factory=list)
    base_os_name: str = "debian"
    base_os_version: str = "bookworm-slim"
    cpu: str = "500m"
    memory: str = "512Mi"
    storage: str = "1Gi"
    ports: list[PortSlot] = field(default_factory=list)
    protocols: list[str] = field(default_factory=lambda: ["HTTP"])
    architectures: list[str] = field(default_factory=lambda: ["x86_64", "arm64"])
    security_level: str = "medium"
    data_classification: str = "private"
    highly_available: bool = False
    startup_time: str = "10s"
    availability: str = "99.0%"


@dataclass
class DeviceSlots:
    name: str = "my-device-app"
    version: str = "1.0.0"
    description: str = ""
    owner: str = "HyperAI-User"
    lifecycle_phase: str = "development"
    intent: str = "device-deployment"
    domain: str = "edge"
    workload_kind: str = "DockerImage"            # DockerImage | AndroidApk | esp32Binary
    image: str = "nginx:latest"
    pull_policy: str = "IfNotPresent"
    apk_url: str = "https://example.com/app.apk"
    package_name: str = "com.example.app"
    binary_url: str = "https://example.com/firmware.bin"
    chip: str = "esp32"
    flash_method: str = "ota"
    device_name: str = ""
    env: dict[str, str] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)
    ports: list[PortSlot] = field(default_factory=list)
    architectures: list[str] = field(default_factory=lambda: ["amd64", "arm64"])


Slots = NativeSlots | DeviceSlots


@dataclass
class SlotExtraction:
    kind: ProfileKind                   # 'native' or 'device'
    slots: Slots
    from_user: set[str]                 # slot attribute names the user explicitly stated
    notes: list[str]                    # human notes, e.g. 'Assumed a native app profile (say "device app" to switch).'


NATIVE_LLM_KEYS: tuple[str, ...] = (
    "name", "description", "image", "tag", "port", "owner", "lifecycle",
    "cpu", "memory", "storage", "domain", "intent",
)
DEVICE_LLM_KEYS: tuple[str, ...] = (
    "name", "description", "image", "port", "owner", "lifecycle",
    "workload_kind", "package_name", "apk_url", "chip", "domain", "intent",
)

KNOWN_IMAGES: dict[str, dict] = {
    "nginx": {"port": 80, "proto": "TCP", "entry": "nginx", "args": ["-g", "daemon off;"], "protocols": ["HTTP"]},
    "httpd": {"port": 80, "proto": "TCP", "entry": "httpd-foreground", "args": [], "protocols": ["HTTP"]},
    "caddy": {"port": 80, "proto": "TCP", "entry": "caddy", "args": ["run"], "protocols": ["HTTP"]},
    "traefik": {"port": 80, "proto": "TCP", "entry": "traefik", "args": [], "protocols": ["HTTP"]},
    "redis": {"port": 6379, "proto": "TCP", "entry": "redis-server", "args": [], "protocols": ["TCP"]},
    "memcached": {"port": 11211, "proto": "TCP", "entry": "memcached", "args": [], "protocols": ["TCP"]},
    "postgres": {"port": 5432, "proto": "TCP", "entry": "postgres", "args": [], "protocols": ["TCP"]},
    "mysql": {"port": 3306, "proto": "TCP", "entry": "mysqld", "args": [], "protocols": ["TCP"]},
    "mariadb": {"port": 3306, "proto": "TCP", "entry": "mariadbd", "args": [], "protocols": ["TCP"]},
    "mongo": {"port": 27017, "proto": "TCP", "entry": "mongod", "args": [], "protocols": ["TCP"]},
    "influxdb": {"port": 8086, "proto": "TCP", "entry": "influxd", "args": [], "protocols": ["HTTP"]},
    "grafana": {"port": 3000, "proto": "TCP", "entry": "/run.sh", "args": [], "protocols": ["HTTP"]},
    "prometheus": {"port": 9090, "proto": "TCP", "entry": "prometheus", "args": [], "protocols": ["HTTP"]},
    "mosquitto": {"port": 1883, "proto": "TCP", "entry": "mosquitto",
                  "args": ["-c", "/mosquitto/config/mosquitto.conf"], "protocols": ["MQTT"]},
    "rabbitmq": {"port": 5672, "proto": "TCP", "entry": "rabbitmq-server", "args": [], "protocols": ["AMQP"]},
    "minio": {"port": 9000, "proto": "TCP", "entry": "minio", "args": ["server", "/data"], "protocols": ["HTTP"]},
    "wordpress": {"port": 80, "proto": "TCP", "entry": "apache2-foreground", "args": [], "protocols": ["HTTP"]},
    "node": {"port": 3000, "proto": "TCP", "entry": "node", "args": ["server.js"], "protocols": ["HTTP"]},
    "python": {"port": 8000, "proto": "TCP", "entry": "python", "args": ["main.py"], "protocols": ["HTTP"]},
    "hello-world": {"port": 80, "proto": "TCP", "entry": "/hello", "args": [], "protocols": ["HTTP"]},
    "busybox": {"port": 8080, "proto": "TCP", "entry": "sh", "args": ["-c", "sleep infinity"], "protocols": ["TCP"]},
    "alpine": {"port": 8080, "proto": "TCP", "entry": "sh", "args": ["-c", "sleep infinity"], "protocols": ["TCP"]},
    "ubuntu": {"port": 8080, "proto": "TCP", "entry": "sleep", "args": ["infinity"], "protocols": ["TCP"]},
}

_STOPWORDS = {"the", "a", "an", "my", "this", "that", "docker", "image", "file",
             "yaml", "service", "app", "application"}

_LIFECYCLE = (
    ("production", ("production", "prod")),
    ("testing", ("testing", "test")),
    ("development", ("development", "dev")),
)


def slug(text: str, *, default: str = "my-app") -> str:
    """lower-case, non [a-z0-9._-] -> '-', collapse, strip '-', max 63 chars."""
    s = text.lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", s)
    s = re.sub(r"-{2,}", "-", s)
    s = s.strip("-")
    s = s[:63].strip("-")
    return s or default


def _split_tag(image: str) -> tuple[str, str | None]:
    if ":" in image:
        base, _, tag = image.rpartition(":")
        if base and re.fullmatch(r"[A-Za-z0-9._\-]+", tag):
            return base, tag
    return image, None


def _find_image_base(t: str) -> tuple[str, bool, str | None]:
    """Return (base, known, explicit_tag)."""
    for name in sorted(KNOWN_IMAGES, key=len, reverse=True):
        m = re.search(r"\b" + re.escape(name) + r"\s*:\s*([A-Za-z0-9._\-]+)\b", t)
        if m:
            return name, True, m.group(1)
    for name in sorted(KNOWN_IMAGES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", t):
            return name, True, None
    if re.search(r"\bapache\b", t):
        return "httpd", True, None
    m = re.search(
        r"(?:image\s+(?:called|named)?\s*|docker\s+image\s+|using\s+(?:the\s+)?|with\s+(?:the\s+)?)"
        r"([a-z0-9][a-z0-9._/\-]*(?::[A-Za-z0-9._\-]+)?)(?=\s|,|\.|$)"
        r"(?:\s+(?:docker\s+)?image)?",
        t,
    )
    if m:
        cand = m.group(1).rstrip(".-")
        if cand.lower() not in _STOPWORDS and re.fullmatch(r"[a-z0-9][a-z0-9._/\-]*(?::[A-Za-z0-9._\-]+)?", cand):
            base, tag = _split_tag(cand)
            return base, base in KNOWN_IMAGES, tag
    m = re.search(
        r"([a-z0-9][a-z0-9._/\-]*(?::[A-Za-z0-9._\-]+)?)\s+(?:docker\s+)?image\b", t
    )
    if m:
        cand = m.group(1)
        if cand.lower() not in _STOPWORDS:
            base, tag = _split_tag(cand)
            return base, base in KNOWN_IMAGES, tag
    return "nginx", False, None


def _normalise_size(num: str, unit: str) -> str:
    f = float(num)
    u = unit.lower()
    if u in ("mib", "mi", "mb"):
        fam = "Mi"
    elif u in ("gib", "gi", "gb"):
        fam = "Gi"
    else:
        fam = "Ti"
    if f.is_integer():
        return f"{int(f)}{fam}"
    if fam == "Gi":
        return f"{int(round(f * 1024))}Mi"
    if fam == "Mi":
        return f"{int(round(f))}Mi"
    return f"{int(round(f * 1024))}Gi"


_SIZE_RE = r"(\d+(?:\.\d+)?)\s*(mib|mi|mb|gib|gi|gb|tib|ti|tb)\b"


def _device_proto(known: dict | None) -> str:
    if known is not None and known.get("proto") in ("MQTT", "AMQP", "TCP"):
        return str(known["proto"])
    return "HTTP"


def extract_slots(user_text: str, kind: ProfileKind | None = None) -> SlotExtraction:
    """Deterministic extraction (regexes, §5.5). kind None -> detect_kind_from_request. Always returns a complete, renderable slot set."""
    t = user_text.lower()
    kinded = kind or detect_kind_from_request(user_text)
    assert kinded in ("native", "device")
    kind_explicit = kind is not None
    from_user: set[str] = set()
    notes: list[str] = []

    is_device = kinded == "device"
    slots: Slots = DeviceSlots() if is_device else NativeSlots()

    # 2. image
    image_base, image_known, explicit_tag = _find_image_base(t)
    image_found = (
        image_base != "nginx"
        or explicit_tag is not None
        or re.search(r"\bnginx\b", t) is not None
    )
    if is_device:
        assert isinstance(slots, DeviceSlots)
        tag = explicit_tag or "latest"
        slots.image = f"{image_base}:{tag}"
        if image_found:
            from_user.add("image")
    else:
        assert isinstance(slots, NativeSlots)
        slots.image_uri = image_base
        slots.image_tag = explicit_tag or "latest"
        if image_found:
            from_user.add("image_uri")
            from_user.add("image_tag")
            from_user.add("image")
    known = KNOWN_IMAGES.get(image_base)

    # 3. tag (only when preceded within 25 chars by the image name)
    tag_span: tuple[int, int] | None = None
    if explicit_tag is not None:
        from_user.add("image_tag" if not is_device else "image")
    else:
        for pat in (r"tag\s+(?:to\s+|of\s+)?([A-Za-z0-9._\-]+)",
                    r"version\s+(\d[\w.\-]*)"):
            m = re.search(pat, t)
            if m and image_base in t[max(0, m.start() - 25):m.start()]:
                tag = m.group(1)
                tag_span = (m.start(1), m.end(1))
                if is_device:
                    assert isinstance(slots, DeviceSlots)
                    slots.image = f"{image_base}:{tag}"
                    from_user.add("image")
                else:
                    assert isinstance(slots, NativeSlots)
                    slots.image_tag = tag
                    from_user.add("image_tag")
                    from_user.add("image")
                break

    # known-image entry/args/protocols (native)
    if not is_device:
        assert isinstance(slots, NativeSlots)
        if known is not None:
            slots.entry_point = str(known["entry"])
            slots.args = list(known["args"])
            slots.protocols = list(known["protocols"])
        else:
            slots.entry_point = image_base
            slots.args = []
            slots.protocols = ["HTTP"]

    # 4. port
    port: int | None = None
    for m in re.finditer(
        r"(?:port\s*(?:number\s*)?(?:to\s+|of\s+|on\s+|is\s+|=\s*|:\s*)?|on\s+port\s+|expose[sd]?\s+(?:port\s+)?)(\d{1,5})",
        t,
    ):
        n = int(m.group(1))
        if 1 <= n <= 65535:
            port = n
            break
    if port is not None:
        from_user.add("port")
    else:
        port = int(known["port"]) if known is not None else 8080
    if is_device:
        assert isinstance(slots, DeviceSlots)
        slots.ports = [PortSlot(port, _device_proto(known))]
    else:
        assert isinstance(slots, NativeSlots)
        proto = str(known["proto"]) if known is not None else "TCP"
        slots.ports = [PortSlot(port, proto)]

    # 5. name
    m = re.search(r"(?:named|called|name\s+(?:it\s+)?|name:\s*)\s*[\"']?([A-Za-z0-9][\w.\-]{0,62})[\"']?", user_text, re.I)
    if m:
        slots.name = slug(m.group(1))
        from_user.add("name")
    else:
        slots.name = slug(f"{image_base}-{'device' if is_device else 'service'}")
    image_title = image_base.capitalize()
    if is_device:
        assert isinstance(slots, DeviceSlots)
        slots.description = f"{image_title} workload for a HyperAI device."
    else:
        assert isinstance(slots, NativeSlots)
        slots.description = f"{image_title} service deployed on the HyperAI platform."

    # 6. version / owner / lifecycle
    consumed = tag_span
    for m in re.finditer(r"\bversion\s+v?(\d+\.\d+\.\d+)\b", t):
        if consumed and not (m.end(1) <= consumed[0] or m.start(1) >= consumed[1]):
            continue
        slots.version = m.group(1)
        from_user.add("version")
        break
    m = re.search(
        r"owner\s+(?:is\s+|to\s+|=\s*)?[\"']?([A-Za-z][\w .\-]{1,38}?)[\"']?(?=[,.;]|\s+(?:and|with|for)\b|$)",
        user_text, re.I,
    )
    if m:
        slots.owner = m.group(1).strip()
        from_user.add("owner")
    m = re.search(r"\b(production|testing|development|prod|dev|test)\b", t)
    if m:
        word = m.group(1)
        for canonical, aliases in _LIFECYCLE:
            if word in aliases:
                slots.lifecycle_phase = canonical
                from_user.add("lifecycle_phase")
                from_user.add("lifecycle")
                break

    # 7. cpu / memory / storage
    cpu: str | None = None
    m = re.search(r"(\d+)\s*m(?:illi)?\s*cores?\b", t)
    if m:
        cpu = f"{int(m.group(1))}m"
    else:
        for m in re.finditer(r"\b(\d+)m\b", t):
            window = t[max(0, m.start() - 30):m.end() + 30]
            if re.search(r"\bcpu\b", window):
                cpu = f"{int(m.group(1))}m"
                break
    if cpu is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:cpu|cpus|cores?|vcpu)\b", t)
        if m:
            cpu = f"{int(round(float(m.group(1)) * 1000))}m"
    if cpu is not None and not is_device:
        assert isinstance(slots, NativeSlots)
        slots.cpu = cpu
        from_user.add("cpu")

    def _size_near(words: str, after: str | None = None) -> str | None:
        for m in re.finditer(_SIZE_RE, t):
            start, end = m.start(), m.end()
            window = t[max(0, start - 30):end + 30]
            near = re.search(words, window) is not None
            direct = after is not None and re.match(rf"\s*(?:{after})\b", t[end:end + 15]) is not None
            if near or direct:
                return _normalise_size(m.group(1), m.group(2))
        return None

    mem = _size_near(r"memory|ram|\bmem\b", r"memory|ram")
    if mem is not None and not is_device:
        assert isinstance(slots, NativeSlots)
        slots.memory = mem
        from_user.add("memory")
    stor = _size_near(r"storage|disk", r"storage|disk")
    if stor is not None and not is_device:
        assert isinstance(slots, NativeSlots)
        slots.storage = stor
        from_user.add("storage")

    # 10. device-only fields (workload kind needed for arch defaults below)
    workload = "DockerImage"
    if is_device:
        assert isinstance(slots, DeviceSlots)
        if re.search(r"android|apk", t):
            workload = "AndroidApk"
            from_user.add("workload_kind")
        elif re.search(r"esp32|firmware|\.bin\b", t):
            workload = "esp32Binary"
            from_user.add("workload_kind")
        slots.workload_kind = workload
        m = re.search(r"https?://\S+\.apk\b", user_text, re.I)
        if m:
            slots.apk_url = m.group(0).rstrip(".,;)")
            from_user.add("apk_url")
        m = re.search(r"https?://\S+\.bin\b", user_text, re.I)
        if m:
            slots.binary_url = m.group(0).rstrip(".,;)")
            from_user.add("binary_url")
        m = re.search(r"com\.[\w.]+", user_text)
        if m:
            slots.package_name = m.group(0).rstrip(".")
            from_user.add("package_name")
        m = re.search(r"esp32(?:s2|s3|c3|c6|h2)?", t)
        if m:
            slots.chip = m.group(0)
            from_user.add("chip")
        m = re.search(r"device\s+(?:named|called)\s+([\w.\-]+)", user_text, re.I)
        if m:
            slots.device_name = m.group(1)
            from_user.add("device_name")

    # 8. architectures
    has_arm = re.search(r"arm64|aarch64", t) is not None
    has_x86 = re.search(r"amd64|x86_64|x86", t) is not None
    if has_arm or has_x86:
        from_user.add("architectures")
        if not is_device:
            assert isinstance(slots, NativeSlots)
            arch: list[str] = []
            if has_x86:
                arch.append("x86_64")
            if has_arm:
                arch.append("arm64")
            slots.architectures = arch
        else:
            assert isinstance(slots, DeviceSlots)
            if workload == "AndroidApk":
                arm = "arm64-v8a"
            elif workload == "esp32Binary":
                arm = "esp32"
            else:
                arm = "arm64"
            arch = []
            if has_x86:
                arch.append("amd64")
            if has_arm:
                arch.append(arm)
            slots.architectures = arch
    elif is_device:
        assert isinstance(slots, DeviceSlots)
        if workload == "AndroidApk":
            slots.architectures = ["arm64-v8a"]
        elif workload == "esp32Binary":
            slots.architectures = ["esp32"]

    # 9. flags
    if re.search(r"highly available|high availability|redundan", t):
        if not is_device:
            assert isinstance(slots, NativeSlots)
            slots.highly_available = True
    if re.search(r"private|internal only|not public|no public", t):
        for p in slots.ports:
            p.public = False
    if re.search(r"secure|high security", t):
        if not is_device:
            assert isinstance(slots, NativeSlots)
            slots.security_level = "high"

    # 11. notes
    if not kind_explicit and not DEVICE_CUES.search(user_text) and not re.search(r"\bnative\b", t):
        notes.append('Assumed a native app profile (say "device app" to make it a device manifest).')
    if "owner" not in from_user:
        notes.append(f"Used the default owner: {slots.owner}.")
    if "port" not in from_user:
        notes.append(f"Used the default port: {slots.ports[0].port}.")
    if not image_known and image_base != "nginx":
        notes.append(f"Unknown image '{image_base}'; assumed defaults (port 8080).")

    return SlotExtraction(kind=kinded, slots=slots, from_user=from_user, notes=notes)


_LLM_VALIDATORS_KIND = {"DockerImage", "AndroidApk", "esp32Binary"}
_LLM_CHIPS = {"esp32", "esp32s2", "esp32s3", "esp32c3", "esp32c6", "esp32h2"}
_LLM_LIFECYCLES = {"development", "testing", "production"}


def _blocked(key: str, attr: str, from_user: set[str]) -> bool:
    return key in from_user or attr in from_user


def _valid_str(v: object, lo: int = 1, hi: int = 120) -> str | None:
    if not isinstance(v, str):
        return None
    if not (lo <= len(v) <= hi):
        return None
    if "\n" in v or "\r" in v:
        return None
    return v


def merge_llm_slots(extraction: SlotExtraction, llm_json: dict) -> SlotExtraction:
    """Apply validated model-proposed values for keys in NATIVE_LLM_KEYS / DEVICE_LLM_KEYS, never overriding a field in extraction.from_user."""
    keys = NATIVE_LLM_KEYS if extraction.kind == "native" else DEVICE_LLM_KEYS
    slots = copy.deepcopy(extraction.slots)
    from_user = set(extraction.from_user)
    notes = list(extraction.notes)

    def take(key: str, attr: str, value: object) -> None:
        setattr(slots, attr, value)
        from_user.add(attr)
        from_user.add(key)
        notes.append(f"Took {key} from your request.")

    for key in keys:
        if key not in llm_json:
            continue
        v = llm_json[key]
        if key == "name":
            if isinstance(v, str) and re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,62}", slug(v)):
                if not _blocked(key, "name", from_user):
                    take(key, "name", slug(v))
        elif key == "image":
            if isinstance(v, str) and re.fullmatch(r"[a-z0-9][a-z0-9._/\-]*(:[A-Za-z0-9._\-]+)?", v):
                base, tag = _split_tag(v)
                if extraction.kind == "native":
                    assert isinstance(slots, NativeSlots)
                    if not _blocked(key, "image_uri", from_user):
                        slots.image_uri = base
                        from_user.add("image_uri")
                        from_user.add(key)
                        notes.append(f"Took {key} from your request.")
                        if tag is not None and not _blocked("tag", "image_tag", from_user):
                            slots.image_tag = tag
                            from_user.add("image_tag")
                else:
                    assert isinstance(slots, DeviceSlots)
                    if not _blocked(key, "image", from_user):
                        take(key, "image", v)
        elif key == "tag":
            if extraction.kind == "native" and isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9._\-]{1,40}", v):
                assert isinstance(slots, NativeSlots)
                if not _blocked(key, "image_tag", from_user):
                    take(key, "image_tag", v)
        elif key == "port":
            n: int | None = None
            if isinstance(v, bool):
                n = None
            elif isinstance(v, int):
                n = v
            elif isinstance(v, str) and re.fullmatch(r"\d+", v.strip()):
                n = int(v.strip())
            if n is not None and 1 <= n <= 65535:
                if not _blocked(key, "ports", from_user):
                    if slots.ports:
                        slots.ports[0].port = n
                    else:
                        proto = "TCP" if extraction.kind == "native" else "HTTP"
                        slots.ports = [PortSlot(n, proto)]
                    from_user.add("ports")
                    from_user.add(key)
                    notes.append(f"Took {key} from your request.")
        elif key in ("owner", "description", "domain", "intent"):
            s = _valid_str(v)
            if s is not None and not _blocked(key, key, from_user):
                take(key, key, s)
        elif key == "lifecycle":
            if isinstance(v, str) and v in _LLM_LIFECYCLES:
                if not _blocked(key, "lifecycle_phase", from_user):
                    slots.lifecycle_phase = v
                    from_user.add("lifecycle_phase")
                    from_user.add(key)
                    notes.append(f"Took {key} from your request.")
        elif key == "cpu":
            if isinstance(v, str) and re.fullmatch(PATTERNS["cpu_m"], v):
                if extraction.kind == "native" and not _blocked(key, "cpu", from_user):
                    assert isinstance(slots, NativeSlots)
                    take(key, "cpu", v)
        elif key in ("memory", "storage"):
            if isinstance(v, str) and re.fullmatch(PATTERNS["mem"], v):
                if extraction.kind == "native" and not _blocked(key, key, from_user):
                    take(key, key, v)
        elif key == "workload_kind":
            if isinstance(v, str) and v in _LLM_VALIDATORS_KIND:
                if extraction.kind == "device" and not _blocked(key, "workload_kind", from_user):
                    assert isinstance(slots, DeviceSlots)
                    take(key, "workload_kind", v)
        elif key in ("apk_url", "binary_url"):
            attr = "apk_url" if key == "apk_url" else "binary_url"
            if isinstance(v, str) and re.fullmatch(PATTERNS["uri"], v):
                if extraction.kind == "device" and not _blocked(key, attr, from_user):
                    take(key, attr, v)
        elif key == "package_name":
            if isinstance(v, str) and re.fullmatch(r"[a-z][\w]*(\.[a-z][\w]*)+", v):
                if extraction.kind == "device" and not _blocked(key, "package_name", from_user):
                    take(key, "package_name", v)
        elif key == "chip":
            if isinstance(v, str) and v in _LLM_CHIPS:
                if extraction.kind == "device" and not _blocked(key, "chip", from_user):
                    take(key, "chip", v)

    return SlotExtraction(kind=extraction.kind, slots=slots, from_user=from_user, notes=notes)
