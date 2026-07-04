# Phase 2: AI Native Core — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Inject soul into Soft-Mianmian — persona, persistent memory, proactive engine. Build `brain.py` as the single orchestration hub that app.py routes call.

**Architecture:** 5 new modules in `backend/`:
- `ai_provider.py` — pluggable AI client (DeepSeek now, voice/multimodal later)
- `persona.py` — soft-mianmian system prompt + grounded context builder
- `memory.py` — SQLite-backed persistent memory (user facts, conversation summaries, environment notes)
- `events.py` — event detection engine (change/timer/event triggers)
- `brain.py` — the hub: orchestrates persona + memory + events + AI → returns companion payloads

All new modules are independently testable. `brain.py` is the only module that app.py imports.

**Tech Stack:** Python 3.12, SQLite (stdlib `sqlite3`), DeepSeek API (OpenAI-compatible)

## Global Constraints

- 下位机程序保持不变
- 通讯协议不变
- Python interpreter: use venv at `d:/VScode/Sleep/venv/Scripts/python`
- All new code lives in `backend/`
- existing `app.py`, `quality.py`, `state.py`, `parser.py`, `serial_io.py` are NOT modified in Phase 2 (except minimal wiring in app.py routes in the final step)
- AI failure → graceful fallback to rule-based companion (existing `build_local_companion`)
- Memory write failure → log & continue, never block the companion
- Gentle-tone constraint and anti-hallucination constraint from the spec are hard rules in persona.py

---
