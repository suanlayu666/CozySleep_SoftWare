"""Centralized configuration — all os.getenv calls live here."""
import os
from dotenv import load_dotenv

load_dotenv()

SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
BAUD_RATE = int(os.getenv("BAUD_RATE", "115200"))
DEVICE_TIMEOUT = int(os.getenv("DEVICE_TIMEOUT", "10"))
APP_PORT = int(os.getenv("APP_PORT", "5000"))
HISTORY_MAX = int(os.getenv("HISTORY_MAX", "120"))
RAW_LOG_MAX = int(os.getenv("RAW_LOG_MAX", "80"))
MOCK_SERIAL = os.getenv("MOCK_SERIAL", "false").strip().lower() in {"1", "true", "yes", "on"}

AI_API_BASE = os.getenv("AI_API_BASE", "")
AI_API_KEY = os.getenv("AI_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "deepseek-chat")
COMPANION_AI_INTERVAL = int(os.getenv("COMPANION_AI_INTERVAL", "35"))
