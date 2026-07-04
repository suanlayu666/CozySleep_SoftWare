# 软眠眠 · 宿舍环境 AI 陪伴系统

STM32 传感器采集 → Python 后端 → Web 前端桌宠 + AI 智能陪伴（带本地规则兜底）

## 项目结构

```
d:\VScode\Sleep\
├── index.html              # 前端页面（麻薯桌宠 + 对话 + 数据抽屉）
├── app.py                  # Flask 路由 + 启动入口（~300 行）
├── backend/                # 后端模块
│   ├── config.py           # 环境变量集中管理
│   ├── state.py            # 共享状态 + 锁
│   ├── parser.py           # STM32 帧解析（JSON / CSV 兜底）
│   ├── quality.py          # 规则质量判断（事实卡片）
│   ├── serial_io.py        # 串口读取 + 模拟器
│   ├── companion.py        # 本地规则伴生逻辑
│   ├── persona.py          # 软眠眠人设 + Prompt 构建
│   ├── ai_provider.py      # 可插拔 AI 客户端（DeepSeek）
│   ├── memory.py           # SQLite 长期记忆
│   ├── events.py           # 主动性事件检测引擎
│   └── brain.py            # 大脑中枢（整合以上所有模块）
├── tests/                  # 特征测试
│   ├── test_parser.py      # 解析器黄金值测试
│   └── test_quality.py     # 质量判断黄金值测试
├── requirements.txt        # Python 依赖
├── .env.example            # 配置模板
└── README.md
```

## 数据流

```
STM32F1 ---串口(COMx@115200)---> serial_io.py ---> parser.py ---> quality.py
                                         │              │              │
                                         ▼              ▼              ▼
                                    state.py     事实卡片       规则判断
                                         │              │              │
                                         └──────────────┴──────┬───────┘
                                                               ▼
                                                          brain.py
                                                         /    │    \
                                               persona.py  memory.py  events.py
                                                    │         │         │
                                                    └────┬────┴────┬────┘
                                                         ▼         ▼
                                                   ai_provider.py  (事实卡片)
                                                         │
                                                         ▼
                                                   DeepSeek API
                                                         │
                                                         ▼
                                                     app.py (Flask)
                                                         │
                                                    SSE / HTTP
                                                         │
                                                         ▼
                                                    index.html
                                                  (麻薯软眠眠桌宠)
```

## 传感器字段

| 字段 | 别名 | 单位 | 范围 | 说明 |
|------|------|------|------|------|
| temperature | temp | °C | -20~80 | DHT11 温度 |
| humidity | humi | % | 0~100 | DHT11 湿度 |
| mq135_adc | air_adc | ADC | 0~4095 | MQ135 原始值,越小越差 |
| mq135_mv | air_mv | mV | 0~3600 | MQ135 毫伏值 |
| motion | pir | 0/1 | 0~1 | 人体红外,1=有人 |
| sound_db | db, decibel | dB | 0~130 | 声音分贝 |

## 快速开始

### 1. 安装依赖

```bash
cd d:\VScode\Sleep
venv\Scripts\activate
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
copy .env.example .env
```

编辑 `.env`：
- `SERIAL_PORT` — 串口号,默认 COM3
- `BAUD_RATE` — 波特率,默认 115200
- `MOCK_SERIAL=true` — 无硬件时用模拟数据
- `AI_API_BASE` / `AI_API_KEY` — DeepSeek API,留空用本地规则

### 3. 启动

```bash
python app.py
```

浏览器打开 `http://127.0.0.1:5000`。

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/sensor` | GET | 最新传感器数据 + 设备状态 |
| `/api/status` | GET | 同 sensor |
| `/api/history` | GET | 最近 N 条历史数据 |
| `/api/raw-log` | GET | 原始串口日志 |
| `/api/serial/ports` | GET | 可用串口列表 |
| `/api/companion/state` | GET | 伴宠完整状态（quality + companion + conversation） |
| `/api/companion/chat` | POST | 用户聊天（AI 优先,规则兜底） |
| `/api/ai-analyze` | POST | 基于历史平均值的 AI 分析 |
| `/api/stream` | GET | SSE 实时推送 |
| `/api/memory/facts` | GET | 长期记忆列表 |
| `/api/memory/reset` | POST | 清空记忆 |

## AI Native 架构

系统采用 **事实与灵魂分层** 设计：

- **规则层（事实）**：`quality.py` 对传感器数值做客观判断,生成"事实卡片"——保证软眠眠从不编造数据
- **AI 层（灵魂）**：`brain.py` 整合 persona + memory + events,让 DeepSeek 以"黏人温柔小女友"的语气说话
- **兜底**：AI 失败/未配 → 自动退回 `companion.py` 模板规则语气

### 软眠眠能力

- **温柔人设**：语气柔和、不生硬、不说教（persona.py 硬约束）
- **防编造**：只基于事实卡片说话,不推断时间/门窗/行为
- **长期记忆**：对话中自动提取用户事实,满 15 条自动压缩（SQLite）
- **主动搭话**：检测到回宿舍/深夜未眠/湿度连升/空气转差时主动关心
- **SSE 实时推送**：前端通过 SSE 接收状态更新,无需轮询

## STM32 串口协议

STM32 通过 USART1 @ 115200 发送 JSON 单行帧,每行以 `\n` 结尾：

```json
{"temp":26,"humi":61,"mq135_adc":2886,"mq135_mv":2329,"motion":1,"sound_db":48}
```

DHT11 读取失败时发送带 error 字段的帧：

```json
{"error":"DHT11_Failed","mq135_adc":2886,"motion":1,"sound_db":48}
```

也支持逗号分隔的 CSV 兜底：`26,61,2886,2329,1,48`

## 测试

```bash
venv\Scripts\activate
python -m pytest tests/ -v
```
