# 团子表情与微动作增强 — 设计文档

日期: 2026-07-04
状态: 待确认

## 1. 目标

在现有 5 种基础情绪(cozy/concerned/alert/warning/offline)上,新增 5 种情绪,每种配备 CSS 面部表情 + 2 个随机微动作。粒子效果暂不实现。

## 2. 情绪体系(共 10 种)

### 原有 5 种(保留)

| 情绪 | 触发 | 表情 |
|------|------|------|
| offline | 设备离线 | 闭眼(Zzz)、灰背景 |
| warning | 帧不完整 | 眼睛微倾、缩小嘴 |
| alert | 环境 critical | 圆眼、张嘴、红底 |
| concerned | 环境 warning | 眼睛微倾、嘴缩 |
| cozy | 一切正常 | 弯眼、大嘴、粉底 |

### 新增 5 种

| 情绪 | 触发时机 | 表情特征 |
|------|----------|----------|
| **happy** | 环境舒适(cozy 延续) + 用户刚聊完天 | 弯弯眼(更弯)、嘴更大、腮红加深 |
| **sleepy** | 设备离线超过 30 秒 | 半闭眼、嘴缩小、身体略缩小 |
| **shy** | 被戳后 3 秒 | 眼睛闪亮、腮红加深、微微缩小 |
| **surprised** | 传感器数值突跳(quality 跳变或 motion 0→1) | 眼睛变大变圆、身体弹起、嘴张大 |
| **listening** | 用户正在输入(聚焦输入框) | 身体微前倾、眼睛稍大、嘴微张 |

### 情绪优先级

当多个情绪同时触发时,优先级:alert > surprised > shy > happy > listening > cozy > concerned > warning > sleepy > offline

## 3. 微动作系统

每个情绪有 2 个微动作变体。微动作**不替换**基础表情,而是在其上叠加短动画。

- 触发间隔:随机 8~15 秒
- 动画时长:0.4~1.2 秒
- 随机选出 1 个微动作执行

| 情绪 | 微动作 A | 微动作 B |
|------|----------|----------|
| cozy | `wobble` 轻微左右晃 | `tilt` 歪头 |
| happy | `bounce` 轻轻弹一下 | `wiggle` 小幅度扭扭 |
| concerned | `sigh` 缩→放(叹气) | `lean` 微微前倾 |
| alert | `shake` 快速抖两下 | `jolt` 猛弹一下 |
| offline | `nod` 头点一下(打瞌睡) | `yawn` 张大→缩小 |
| sleepy | `nod` 头点一下 | `droop` 身体下坠 |
| shy | `shrink` 往后缩 | `quiver` 微微发抖 |
| surprised | `pop` 弹起变大 | `freeze` 突然静止 |
| listening | `lean_in` 身体微前倾 | `fast_blink` 连眨两下 |
| warning | `twitch` 微抽一下 | `scan` 左右扫视 |

## 4. 触发逻辑

### 4.1 后端新增 emotion 值

在 `build_local_companion` 中新增 5 个 emotion 返回值:
- `happy`:当 quality.overall_level == "normal" 且 frame.complete 时
- `sleepy`:当 !online 时(替代原 offline),离线超过 30 秒从 offline → sleepy
- `surprised`:当 quality 前次与本次 overall_level 跳变时

### 4.2 前端新增触发

- `shy`:用户点击团子后,设置 3 秒 shy 状态,然后恢复
- `listening`:用户聚焦输入框时设置,失焦后恢复

## 5. 技术实现

### CSS

- 每种新情绪一个 `.companion.<emotion>` 选择器
- 微动作用独立 CSS class:`.micro-<name>`,通过 JS 动态添加/移除
- 使用 CSS `@keyframes` 定义微动作动画

### JS

- `microTimer`:每 8~15 秒随机触发微动作
- `updateCompanion()`:扩展以支持新情绪映射
- 输入框 focus/blur 事件:设置/清除 listening 状态

## 6. 不包含

- 粒子特效(开心冒爱心等)——后续版本
- AI 驱动情绪变化——当前由规则层决定 emotion
