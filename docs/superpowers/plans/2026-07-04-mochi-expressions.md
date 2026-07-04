# Mochi Expression & Micro-Animation — Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task. Each step is checkbox (`- [ ]`) tracked.

**Goal:** Add 5 new emotions (happy/sleepy/shy/surprised/listening) to the mochi pet, each with CSS facial expressions + 2 random micro-animations triggered every 8-15s.

**Architecture:** Backend `companion.py` emits new emotion values. Frontend `index.html` maps them to CSS classes and runs a micro-animation timer. No new files — all changes in companion.py and index.html.

**Tech Stack:** CSS keyframes, vanilla JS, Python (companion.py)

## Global Constraints

- 不新增依赖
- 不改下位机协议
- 原有 5 种情绪(cozy/concerned/alert/warning/offline)行为不变
- 微动作不替换基础表情,叠加其上
- 每完成一个模块用户本地测试通过再 commit

---

### Task 1: Backend — new emotion values in companion.py

**Files:** Modify `backend/companion.py`

Add `happy` and `sleepy` emotions. `surprised` is triggered by events.py already (quality jump events). The mapping:
- `cozy` stays for normal
- `happy` replaces cozy when user just chatted (set by brain.py with a flag)
- `sleepy` replaces offline when device offline >30s
- `surprised` — quality jump event detected

Simplest approach: add `happy` as a variant of cozy when frame is complete and normal, and add `sleepy` alongside offline. The frontend will also handle `shy` and `listening` purely in JS.

### Task 2: CSS — new emotion facial expressions

**Files:** Modify `index.html` `<style>` section

Add 5 new `.companion.<emotion>` selectors:

- `.companion.happy` — eyes more curved (larger border-radius), mouth wider, blush deeper
- `.companion.sleepy` — eyes half-height, body slightly scaled down, slow blink
- `.companion.shy` — eyes smaller with highlight, blush stronger, slight shrink
- `.companion.surprised` — eyes large round, body scaled up, mouth oval
- `.companion.listening` — eyes slightly larger, body micro-lean-forward, mouth tiny open

### Task 3: CSS — micro-animation keyframes

**Files:** Modify `index.html` `<style>` section

Add `@keyframes` for all 19 micro-animations: wobble, tilt, bounce, wiggle, sigh, lean, shake, jolt, nod, yawn, droop, shrink, quiver, pop, freeze, lean_in, fast_blink, twitch, scan.

Each is a short (0.4-1.2s) CSS animation applied via class `.micro-<name>`.

### Task 4: JS — micro-animation timer

**Files:** Modify `index.html` `<script>` section

Add `startMicroTimer()` function that:
1. Every 8-15s randomly, picks a micro-action for current emotion
2. Adds `.micro-<name>` class to companion element
3. Removes it after animation duration
4. Restarts timer

### Task 5: JS — listening + shy triggers

**Files:** Modify `index.html` `<script>` section

- Input focus → set `listening` emotion, blur → restore
- Click on companion → set `shy` for 3s, then restore
- Update `updateCompanion` to handle new emotion values from backend

---
