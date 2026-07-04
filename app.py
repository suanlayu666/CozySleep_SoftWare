"""
环境传感器 AI 分析系统 - 后端服务
功能：读取 STM32F1 串口数据 + 提供 REST API + AI 分析（带本地规则兜底）
"""

import serial  
import threading
import time
import json
import os
from datetime import datetime
from collections import deque

import requests 
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ==================== 配置 ====================
SERIAL_PORT = os.getenv('SERIAL_PORT', 'COM3')
BAUD_RATE = int(os.getenv('BAUD_RATE', '115200'))
DEVICE_TIMEOUT = int(os.getenv('DEVICE_TIMEOUT', '10'))  # 超过该秒数无数据则判定离线

# AI 配置（OpenAI 兼容格式，支持 DeepSeek / 智谱 / 通义 / OpenAI 等）
AI_API_BASE = os.getenv('AI_API_BASE', '')   # 如 https://api.deepseek.com/v1
AI_API_KEY = os.getenv('AI_API_KEY', '')
AI_MODEL = os.getenv('AI_MODEL', 'deepseek-chat')

# ==================== 全局状态 ====================
latest_data = {
    'temperature': 0,     # 温度 °C
    'humidity': 0,        # 空气湿度 %
    'sound_db': 0,        # 声音分贝 dB
    'mq135_adc': 0,       # 空气质量 ADC 值（越小越差）
    'mq135_mv': 0,        # 空气质量电压 mV
    'motion': 0,          # 人体检测（1有/0无）
    'soil': 0,            # 土壤湿度 %（可能无）
}
last_update = 0.0
data_lock = threading.Lock()
ser = None

# 历史数据缓存（保留最近 20 条，约 20 秒，用于趋势图）
HISTORY_MAX = 20
history = deque(maxlen=HISTORY_MAX)
history_lock = threading.Lock()


# ==================== 串口读取线程 ====================
def serial_reader():
    """持续读取 STM32 串口数据，解析后更新全局缓存"""
    global latest_data, last_update, ser
    print(f"[串口] 尝试连接 {SERIAL_PORT}@{BAUD_RATE} ...")
    while True:
        try:
            if ser is None or not ser.is_open:
                ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
                print(f"[串口] 已连接 {SERIAL_PORT}@{BAUD_RATE}")

            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue

            data = parse_line(line)
            if data:
                with data_lock:
                    latest_data = data
                    last_update = time.time()
                with history_lock:
                    history.append({
                        'time': datetime.now().strftime('%H:%M:%S'),
                        'timestamp': round(time.time(), 1),
                        **data,
                    })
        except Exception as e:
            print(f"[串口] 读取错误: {e}")
            if ser and ser.is_open:
                ser.close()
            ser = None
            time.sleep(3)  # 等待重连


def parse_line(line):
    """
    解析串口数据行，支持两种格式：
    1. JSON:   {"temp":26,"humi":95,"mq135_adc":2794,"mq135_mv":2249,"motion":1,"sound_db":33}
    2. 逗号分隔: temp,humi,sound_db,mq135_adc,mq135_mv,motion,soil（按顺序，缺省为0）
    
    所有字段均为可选，缺失字段默认为0，确保程序健壮性。
    """
    # 默认值
    result = {
        'temperature': 0.0,
        'humidity': 0.0,
        'sound_db': 0.0,
        'mq135_adc': 0.0,
        'mq135_mv': 0.0,
        'motion': 0.0,
        'soil': 0.0,
    }

    # 尝试 JSON 解析
    try:
        obj = json.loads(line)
        if 'temp' in obj or 'temperature' in obj:
            result['temperature'] = float(obj.get('temp', obj.get('temperature', 0)))
        if 'humi' in obj or 'humidity' in obj:
            result['humidity'] = float(obj.get('humi', obj.get('humidity', 0)))
        if 'db' in obj or 'decibel' in obj:
            result['sound_db'] = float(obj.get('db', obj.get('decibel', 0)))
        if 'sound_db' in obj:
            result['sound_db'] = float(obj['sound_db'])
        if 'mq135_adc' in obj:
            result['mq135_adc'] = float(obj['mq135_adc'])
        if 'mq135_mv' in obj:
            result['mq135_mv'] = float(obj['mq135_mv'])
        if 'motion' in obj:
            result['motion'] = float(obj['motion'])
        if 'soil' in obj:
            result['soil'] = float(obj['soil'])
        return result
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # 尝试逗号分隔解析（兼容旧格式 + 新扩展格式）
    parts = line.replace(' ', '').split(',')
    if len(parts) >= 2:  # 至少有 temp 和 humi
        try:
            if len(parts) >= 1: result['temperature'] = float(parts[0])
            if len(parts) >= 2: result['humidity'] = float(parts[1])
            if len(parts) >= 3: result['sound_db'] = float(parts[2])
            if len(parts) >= 4: result['mq135_adc'] = float(parts[3])
            if len(parts) >= 5: result['mq135_mv'] = float(parts[4])
            if len(parts) >= 6: result['motion'] = float(parts[5])
            if len(parts) >= 7: result['soil'] = float(parts[6])
            return result
        except (ValueError, IndexError):
            pass

    return None


def is_online():
    """根据最后收到数据的时间判断设备是否在线"""
    return (time.time() - last_update) < DEVICE_TIMEOUT


def calc_average():
    """计算历史缓存中各物理量的平均值；无历史则回退用最新一条数据"""
    with history_lock:
        hist = list(history)
    if not hist:
        with data_lock:
            return dict(latest_data)
    n = len(hist)
    return {
        'temperature': round(sum(h['temperature'] for h in hist) / n, 1),
        'humidity':    round(sum(h['humidity'] for h in hist) / n, 1),
        'sound_db':    round(sum(h['sound_db'] for h in hist) / n, 1),
        'mq135_adc':   round(sum(h['mq135_adc'] for h in hist) / n, 1),
        'mq135_mv':    round(sum(h['mq135_mv'] for h in hist) / n, 1),
        'motion':      round(sum(h['motion'] for h in hist) / n, 1),  # 取平均，>=0.5视为有人
        'soil':        round(sum(h['soil'] for h in hist) / n, 1),
    }


# ==================== API 接口 ====================
@app.route('/api/sensor')
def get_sensor():
    """返回最新传感器数据 + 设备在线状态"""
    with data_lock:
        data = dict(latest_data)
    return jsonify({
        'online': is_online(),
        'data': data,
        'last_update': last_update,
        'last_update_str': datetime.fromtimestamp(last_update).strftime('%H:%M:%S') if last_update else '无数据'
    })


@app.route('/api/history')
def get_history():
    """返回最近 20 条历史数据（用于趋势图）"""
    with history_lock:
        data = list(history)
    return jsonify({'count': len(data), 'history': data})


@app.route('/api/ai-analyze', methods=['POST'])
def ai_analyze():
    """AI 分析接口：使用历史数据平均值，先调 AI API，失败则回退本地规则"""
    avg = calc_average()

    # 优先调用 AI API
    if AI_API_BASE and AI_API_KEY:
        try:
            result = call_ai(avg)
            return jsonify({'ok': True, 'source': 'ai', **result})
        except Exception as e:
            print(f"[AI] 调用失败，回退本地规则: {e}")

    # 本地规则兜底
    result = local_analyze(avg)
    return jsonify({'ok': True, 'source': 'rule', **result})


def call_ai(data):
    """调用 OpenAI 兼容的 AI API"""
    temp = data.get('temperature', 0)
    humi = data.get('humidity', 0)
    db = data.get('sound_db', 0)
    soil = data.get('soil', 0)
    air_adc = data.get('mq135_adc', 0)
    motion = int(data.get('motion', 0))

    motion_text = '检测到有人活动' if motion >= 0.5 else '未检测到人'
    
    prompt = (
        "你现在是我的温柔女友，请用亲密、自然、温暖的语气回复我。"
        "你要多关注我的感受，先共情，再慢慢说事；多用「你、我、我们」，像在陪我聊天，而不是冷冰冰地讲道理。"
        "可以适度撒娇、安慰我，用一点「呀、哦、呢、～」这样的语气词，但保持真实、克制，不要油腻。"
        "无论是分析问题还是给建议，都要让我感觉你在我身边，认真理解我、陪着我。\n\n"

        f"温度：{temp}°C，空气湿度：{humi}%，"
        f"噪声：{db}dB，土壤湿度：{soil}%，"
        f"空气质量ADC值：{air_adc}（值越小空气质量越差），"
        f"人体检测：{motion_text}。"
        f"这是环境的相关物理量，给我一些关心和建议。"
    )

    resp = requests.post(
        f"{AI_API_BASE.rstrip('/')}/chat/completions",
        headers={
            'Authorization': f'Bearer {AI_API_KEY}',
            'Content-Type': 'application/json',
        },
        json={
            'model': AI_MODEL,
            'messages': [
                {'role': 'system', 'content': '你是环境监测助手，根据传感器数据给出简洁、专业的分析和建议。'},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': 400,
            'temperature': 0.7,
        },
        timeout=15,
    )
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content'].strip()

    return {
        'status': 'AI 分析完成',
        'suggestion': content,
    }


def local_analyze(data):
    """本地规则兜底分析（与前端 localAnalyze 逻辑一致）"""
    temp = data.get('temperature', 0)
    humi = data.get('humidity', 0)
    db = data.get('sound_db', 0)
    soil = data.get('soil', 0)
    air_adc = data.get('mq135_adc', 0)
    motion = data.get('motion', 0)

    issues = []
    tips = []

    if temp < 18:
        issues.append('温度偏低')
        tips.append('建议开启暖气或关闭门窗')
    elif temp > 30:
        issues.append('温度偏高')
        tips.append('建议开启空调或通风降温')
    else:
        issues.append('温度适宜')

    if humi < 30:
        issues.append('湿度过低')
        tips.append('建议使用加湿器增加空气湿度')
    elif humi > 70:
        issues.append('湿度过高')
        tips.append('建议开启除湿器或开窗通风')
    else:
        issues.append('湿度适中')

    if db > 60:
        issues.append('噪声超标')
        tips.append('当前噪声较大，建议关闭门窗隔音')
    else:
        issues.append('噪声正常')

    if air_adc > 0 and air_adc < 500:  # ADC值很低说明空气质量差
        issues.append('空气质量较差')
        tips.append('空气污染较重，建议通风或开启空气净化器')
    elif air_adc >= 2000:
        issues.append('空气质量良好')
    
    if motion >= 0.5:
        issues.append('检测到有人活动')
    else:
        issues.append('无人活动')

    # 土壤湿度可能没有传感器，仅在非零时分析
    if soil > 0:
        if soil < 20:
            issues.append('土壤缺水')
            tips.append('土壤湿度过低，请及时浇水')
        elif soil > 80:
            issues.append('土壤过湿')
            tips.append('土壤湿度过高，暂停浇水并保持通风')
        else:
            issues.append('土壤湿度正常')

    status = f'当前状态：{"，".join(issues)}'
    suggestion = '；'.join(tips) + '。' if tips else '各项指标均在正常范围内，建议保持当前环境状态。'

    return {'status': status, 'suggestion': suggestion}


# ==================== 启动 ====================
if __name__ == '__main__':
    # 启动串口读取线程
    t = threading.Thread(target=serial_reader, daemon=True)
    t.start()

    print("[服务] 后端已启动: http://localhost:5000")
    print(f"[AI]   API 配置: {'已配置' if AI_API_BASE and AI_API_KEY else '未配置（将使用本地规则兜底）'}")
    app.run(host='0.0.0.0', port=5000, debug=False)
