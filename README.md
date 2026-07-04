# 环境传感器 AI 分析系统

STM32F1 串口采集环境数据 → Python 后端读取 → 前端展示 + AI 智能分析（带本地规则兜底）

## 项目结构

```
d:\环境监测系统\
├── index.html          # 前端页面
├── app.py              # 后端服务（Flask + pyserial）
├── requirements.txt    # Python 依赖
├── .env.example        # 配置模板（复制为 .env 后填写）
└── README.md
```

## 数据流

```
STM32F1 --串口(COM3@115200)--> app.py --HTTP API--> index.html
                                   |
                                   +--> AI API（失败回退本地规则）
```

## 快速开始

### 1. 安装依赖

```bash
cd d:\环境监测系统
pip install -r requirements.txt
```

### 2. 配置环境变量

```bash
copy .env.example .env
```

编辑 `.env`：
- 串口配置默认 `COM3@115200`，按实际修改
- AI 配置留空也能运行（自动用本地规则兜底），填入后启用真实 AI 分析

### 3. 启动后端

```bash
python app.py
```

看到 `[服务] 后端已启动: http://localhost:5000` 即成功。

### 4. 打开前端

直接用浏览器打开 `index.html` 即可。页面会每 5 秒自动拉取最新传感器数据。

## STM32 端代码示例

STM32 通过串口以 **JSON 单行格式** 发送数据，每行以 `\n` 结尾：

```c
// 推荐格式: {"temp":26.5,"humi":61,"db":48,"soil":42}\n
// 也支持逗号分隔: 26.5,61,48,42\n

#include <stdio.h>

// 通过重定向 printf 到串口，或直接用 sprintf + USART_SendString
void send_sensor_data(float temp, float humi, float db, float soil) {
    printf("{\"temp\":%.1f,\"humi\":%.0f,\"db\":%.0f,\"soil\":%.0f}\r\n",
           temp, humi, db, soil);
}

// 在主循环中定时调用，建议每 2~3 秒发送一次
int main(void) {
    USART_Init(115200);  // 初始化串口
    while (1) {
        float temp = ReadTemp();
        float humi = ReadHumi();
        float db   = ReadDecibel();
        float soil = ReadSoil();
        send_sensor_data(temp, humi, db, soil);
        Delay_ms(2000);
    }
}
```

> 注意：串口波特率必须与 `.env` 中的 `BAUD_RATE` 一致（默认 115200）。

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/sensor` | GET | 返回最新传感器数据 + 设备在线状态 |
| `/api/history` | GET | 返回最近 20 条历史数据（用于趋势图） |
| `/api/ai-analyze` | POST | 基于历史数据平均值进行 AI 分析，失败回退本地规则 |

### 返回示例

`GET /api/sensor`：
```json
{
  "online": true,
  "data": {"temperature": 26.5, "humidity": 61, "decibel": 48, "soil": 42},
  "last_update": 1751558400.0,
  "last_update_str": "20:00:00"
}
```

`POST /api/ai-analyze`：
```json
{
  "ok": true,
  "source": "ai",        // "ai" = AI分析, "rule" = 本地规则兜底
  "status": "AI 分析完成",
  "suggestion": "当前温度湿度适宜，建议..."
}
```

## 兜底机制

系统采用 **三层兜底**，确保任何情况下前端都能正常展示：

1. **后端 AI 兜底**：AI API 调用失败/超时 → 回退本地规则分析
2. **前端后端兜底**：后端服务不可达 → 前端用内置规则分析
3. **设备离线兜底**：STM32 超过 10 秒无数据 → 状态显示"未连接"，但不影响页面操作

## 配置说明

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `SERIAL_PORT` | `COM3` | 串口号 |
| `BAUD_RATE` | `115200` | 波特率 |
| `DEVICE_TIMEOUT` | `10` | 设备离线判定阈值（秒） |
| `AI_API_BASE` | 空 | AI 接口地址，留空用本地规则 |
| `AI_API_KEY` | 空 | AI 密钥 |
| `AI_MODEL` | `deepseek-chat` | 模型名 |
