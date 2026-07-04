"""Brain — the single orchestration hub for Soft-Mianmian.

Imports from persona, memory, ai_provider, quality, state to produce
companion payloads and chat replies. This is the *only* backend module
that app.py routes need to call directly.
"""
import json
import time

from backend.ai_provider import AIError, AIProvider
from backend.quality import build_quality
from backend.memory import (
    get_all_user_facts_as_strings,
    remember_user_fact,
    add_conversation_summary,
)
from backend.persona import (
    build_proactive_prompt,
    build_chat_prompt,
    build_memory_extraction_prompt,
)
from backend.serial_io import now_str
from backend.companion import build_local_companion, local_chat_reply

# ── Module-level state ──
_provider: AIProvider | None = None
_companion_cache = {
    "signature": None,
    "payload": None,
    "created_at": 0.0,
}
_cache_ttl = 35  # seconds — same as old COMPANION_AI_INTERVAL


def init_brain(provider: AIProvider | None):
    """Initialize the brain with an AI provider.

    If provider is None, the brain works in rule-only mode
    (all AI calls fall back gracefully).
    """
    global _provider
    _provider = provider


def _has_ai() -> bool:
    return _provider is not None


# ── Companion Signature (for cache invalidation) ──

def _signature(snapshot: dict) -> str:
    """Build a cache key from the current sensor snapshot."""
    data = snapshot.get("data") or {}

    def bucket(field, step=1):
        value = data.get(field)
        if not isinstance(value, (int, float)):
            return value
        return int(round(value / step) * step)

    payload = {
        "online": snapshot.get("online"),
        "complete": (snapshot.get("frame") or {}).get("complete"),
        "level": (snapshot.get("quality") or {}).get("overall_level"),
        "temperature": bucket("temperature", 1),
        "humidity": bucket("humidity", 2),
        "mq135_adc": bucket("mq135_adc", 80),
        "sound_db": bucket("sound_db", 3),
        "alerts": ((snapshot.get("quality") or {}).get("alerts") or [])[:2],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


# ── AI-powered companion speech ──

def _call_ai_for_companion(snapshot: dict, trigger: str) -> str | None:
    """Ask AI what to say. Returns the message string, or None on failure."""
    if not _has_ai():
        return None

    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or build_quality(data)
    memories = get_all_user_facts_as_strings(limit=5)

    messages = build_proactive_prompt(
        trigger_reason=trigger,
        data=data,
        quality=quality,
        memories=memories,
        events=[],
    )

    try:
        return _provider.chat(messages, temperature=0.7, max_tokens=180)
    except AIError as exc:
        print(f"[Brain] AI companion call failed: {exc}")
        return None


def get_companion_payload(snapshot: dict) -> dict:
    """Get the best available companion payload.

    Caches AI results by sensor signature. Falls back to rule-based
    companion (build_local_companion) when AI is unavailable.
    """
    local_payload = build_local_companion(snapshot)

    if not snapshot.get("online") or not _has_ai():
        return local_payload

    sig = _signature(snapshot)
    now = time.time()

    # Return cached AI result if signature hasn't changed and TTL hasn't expired
    if (
        _companion_cache.get("signature") == sig
        and _companion_cache.get("payload")
        and (now - _companion_cache.get("created_at", 0)) < _cache_ttl
    ):
        return _companion_cache["payload"]

    # Try AI
    level = (snapshot.get("quality") or {}).get("overall_level", "normal")
    if level == "critical":
        trigger = "环境出现异常，需要提醒用户"
    elif level == "warning":
        trigger = "环境有轻微异常，温柔提醒用户"
    else:
        trigger = "环境正常，温柔陪伴用户"

    ai_message = _call_ai_for_companion(snapshot, trigger)
    if ai_message:
        payload = dict(local_payload)
        payload.update({
            "source": "ai",
            "message": ai_message,
            "generated_at": now_str(),
        })
        _companion_cache.update({
            "signature": sig,
            "payload": payload,
            "created_at": now,
        })
        return payload

    # Fallback to rule
    _companion_cache.update({
        "signature": sig,
        "payload": local_payload,
        "created_at": now,
    })
    return local_payload


# ── AI-powered chat ──

def _call_ai_chat(message: str, snapshot: dict, recent_history: list[dict]) -> str | None:
    """Ask AI for a chat reply. Returns None on failure."""
    if not _has_ai():
        return None

    data = snapshot.get("data") or {}
    quality = snapshot.get("quality") or build_quality(data)
    memories = get_all_user_facts_as_strings(limit=5)

    messages = build_chat_prompt(
        message=message,
        data=data,
        quality=quality,
        memories=memories,
        recent_history=recent_history,
    )

    try:
        return _provider.chat(messages, temperature=0.65, max_tokens=360)
    except AIError as exc:
        print(f"[Brain] AI chat call failed: {exc}")
        return None


def _extract_and_store_facts(user_msg: str, assistant_reply: str):
    """Extract long-term facts from every conversation turn and store them.

    Uses AI with robust fallback parsing. Auto-triggers compression at 15 facts.
    """
    if not _has_ai():
        return

    messages = build_memory_extraction_prompt(user_msg, assistant_reply)
    try:
        raw = _provider.chat(messages, temperature=0.3, max_tokens=200)
    except AIError as exc:
        print(f"[Memory] Extraction AI call failed: {exc}")
        return

    facts = _parse_facts(raw)
    if facts:
        for fact in facts:
            if fact.strip():
                remember_user_fact(fact.strip(), source="chat_extraction")
                print(f"[Memory] Stored: {fact.strip()}")
    else:
        print(f"[Memory] No new facts from this turn")

    # Auto-compress when we have 15+ facts
    from backend.memory import count_user_facts
    if count_user_facts() >= 15:
        _compress_facts()


def _parse_facts(raw: str) -> list[str]:
    """Robustly parse facts from AI output — JSON, markdown-fenced JSON, or line-by-line."""
    cleaned = raw.strip()

    # Try 1: plain JSON array
    try:
        result = json.loads(cleaned)
        if isinstance(result, list):
            return [str(f) for f in result if str(f).strip()]
    except (json.JSONDecodeError, TypeError):
        pass

    # Try 2: strip markdown fences
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove opening fence
        if lines[0].startswith("```"):
            lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
        try:
            result = json.loads(cleaned)
            if isinstance(result, list):
                return [str(f) for f in result if str(f).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

    # Try 3: line-by-line — strip bullet markers and quotes
    fact_lines = []
    for line in raw.split("\n"):
        line = line.strip().lstrip("-•·*").strip().strip('"').strip("'").strip()
        if line and len(line) > 2 and not line.startswith("```"):
            fact_lines.append(line)
    if fact_lines:
        return fact_lines

    return []


def _compress_facts():
    """When 15+ facts accumulate, ask AI to merge them into 5-8 dense facts."""
    if not _has_ai():
        return

    from backend.memory import get_all_user_facts_as_strings, get_user_facts, delete_user_fact

    old_facts = get_all_user_facts_as_strings(limit=30)
    if len(old_facts) < 15:
        return

    print(f"[Memory] Compressing {len(old_facts)} facts...")
    compress_prompt = [
        {
            "role": "system",
            "content": (
                "你是一个信息整理助手。将下面关于用户的事实去重、合并、精简。\n"
                "规则：\n"
                "1. 合并相似或重复的信息\n"
                "2. 保留所有不重复的独特信息\n"
                "3. 用简洁的一句话表达每条事实，以'用户'开头\n"
                "4. 返回纯 JSON 数组，不要加任何解释或 markdown\n"
                "5. 目标 5-8 条"
            ),
        },
        {
            "role": "user",
            "content": f"整理以下事实：\n" + "\n".join(f"- {f}" for f in old_facts),
        },
    ]

    try:
        raw = _provider.chat(compress_prompt, temperature=0.4, max_tokens=400)
        compressed = _parse_facts(raw)
        if compressed and len(compressed) >= 3:
            # Delete all old facts
            old = get_user_facts(limit=50)
            for f in old:
                delete_user_fact(f["id"])
            # Store compressed ones
            for fact in compressed:
                if fact.strip():
                    remember_user_fact(fact.strip(), source="compression")
            print(f"[Memory] Compressed {len(old_facts)} → {len(compressed)} facts")
        else:
            print(f"[Memory] Compression returned too few facts, skipped")
    except AIError as exc:
        print(f"[Memory] Compression failed: {exc}")


def chat(message: str, snapshot: dict) -> dict:
    """Handle a user chat message. Returns {'ok', 'source', 'reply'}.

    Uses AI when available, falls back to local rule-based reply.
    """
    companion = build_local_companion(snapshot)

    if _has_ai():
        # Get recent conversation history
        from backend.state import conversation_history, conversation_lock
        with conversation_lock:
            recent = list(conversation_history)[-8:]

        ai_reply = _call_ai_chat(message, snapshot, recent)
        if ai_reply:
            # Extract facts for long-term memory
            _extract_and_store_facts(message, ai_reply)
            return {
                "ok": True,
                "source": "ai",
                "reply": ai_reply,
            }

    # Fallback to local rules
    rule_reply = local_chat_reply(message, snapshot, companion)
    return {
        "ok": True,
        "source": "rule",
        "reply": rule_reply,
    }


# ── Proactive thinking (will be wired to events.py) ──

def think(snapshot: dict) -> dict | None:
    """Called periodically. Returns a companion payload if the brain
    decides to speak proactively, or None to stay silent.

    Currently delegates to events engine; the companion state endpoint
    already covers passive updates via get_companion_payload().
    """
    # TODO: Wire to events.py in Step 2.5
    return None
