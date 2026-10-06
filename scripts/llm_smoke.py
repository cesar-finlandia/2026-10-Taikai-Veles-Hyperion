import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
"""LLM smoke check: ping, chat, embed. Exit 0=all OK, 2=key missing, 1=otherwise."""
import sys
import time
from hyperion.config import load_settings
from hyperion.llm.client import build_llm


def main() -> int:
    settings = load_settings()
    key = settings.api_key or ""
    print(f"base_url: {settings.llm_base_url}")
    print(f"model: {settings.llm_model}")
    print(f"key: {'set (%d chars)' % len(key) if key else 'MISSING'}")
    if not key:
        print("FAIL NoKey: API_KEY is empty")
        return 2
    import asyncio
    llm = build_llm(settings)
    ok_all = True

    async def go() -> bool:
        ok = True
        t0 = time.perf_counter()
        try:
            result = await llm.ping()
            ms = (time.perf_counter() - t0) * 1000
            print(f"ping: {'OK' if result else 'FAIL ping returned False'} {ms:.0f}ms")
            ok = ok and bool(result)
        except Exception as e:
            print(f"ping: FAIL {type(e).__name__}: {e}")
            ok = False
        t0 = time.perf_counter()
        try:
            text = await llm.chat([{"role": "user", "content": "Reply with the single word OK."}],
                                  name="smoke")
            ms = (time.perf_counter() - t0) * 1000
            print(f"chat: OK {ms:.0f}ms reply={text[:80]!r}")
        except Exception as e:
            print(f"chat: FAIL {type(e).__name__}: {e}")
            ok = False
        t0 = time.perf_counter()
        try:
            vecs = await llm.embed(["hello"], kind="query")
            ms = (time.perf_counter() - t0) * 1000
            print(f"embed: OK {ms:.0f}ms dim={len(vecs[0]) if vecs else 0}")
        except Exception as e:
            print(f"embed: FAIL {type(e).__name__}: {e}")
            ok = False
        try:
            await llm.aclose()
        except Exception:
            pass
        return ok

    ok_all = asyncio.run(go())
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
