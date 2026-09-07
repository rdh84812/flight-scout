import os
from pathlib import Path
from typing import Any, Dict
import yaml
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config.yaml"
ENV_PATH = BASE_DIR / ".env"

# 載入 .env 環境變數
load_dotenv(dotenv_path=ENV_PATH)

def load_config() -> Dict[str, Any]:
    """讀取 config.yaml，並與預設值合併"""
    default_config = {
        "google_flights": {
            "prompt": "華航，飛任何地方，桃園機場出發",
            "url": "https://www.google.com/travel/flights/deals?hl=zh-TW",
            "timeout_ms": 30000,
            "modal_appearance_delay_ms": 3000,
            "before_prompt_delay_ms": 3000,
            "submit_delay_ms": 1500,
        },
        "browser": {
            "headless": False,
            "slow_mo": 50,
            "viewport": {"width": 1280, "height": 900},
        },
        "schedule": {
            "enabled": True,
            "hour": 7,
            "minute": 0,
            "timezone": "Asia/Taipei",
        },
        "notification": {
            "min_price_drop": 100,
        },
        "reports": {
            "enabled": True,
            "auto_publish": True,
            "site_url": "https://rdh84812.github.io/flight-scout/",
        },
    }

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f) or {}
        # 深度合併
        for section, values in user_config.items():
            if isinstance(values, dict) and section in default_config:
                default_config[section].update(values)
            else:
                default_config[section] = values

    return default_config

def get_env_var(key: str, default: str = "") -> str:
    """取得環境變數值"""
    return os.getenv(key, default)
