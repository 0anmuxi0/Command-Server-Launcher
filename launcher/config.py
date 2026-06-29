# 配置管理 - 统一 JSON 配置系统
# 所有配置存储在项目根目录 config.json

import os
import json
import configparser
from .logger import log_info, log_debug, log_warn, log_error

# 默认 Minecraft 文件夹路径
DOT_MINECRAFT = os.path.join(os.getenv("APPDATA", ""), ".minecraft")

# 配置文件路径（项目根目录）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = PROJECT_ROOT
CONFIG_JSON = os.path.join(PROJECT_ROOT, "config.json")
SETUP_INI = os.path.join(PROJECT_ROOT, "Setup.ini")
ACCOUNTS_FILE = os.path.join(PROJECT_ROOT, "accounts.json")
CACHE_DIR = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", PROJECT_ROOT)), "CML-Cache")

# 默认配置（launch / general / download 全部统一）
DEFAULT_CONFIG: dict = {
    "launch": {
        "JavaPath": "",
        "MinMemory": "1024",
        "MaxMemory": "4096",
        "GameDirectory": DOT_MINECRAFT,
        "VersionIsolation": "false",
        "JvmArgs": "",
        "WindowWidth": "854",
        "WindowHeight": "480",
        "AutoConnectServer": "",
    },
    "general": {
        "Language": "zh-cn",
        "Theme": "Light",
    },
    "download": {
        "max_threads": 32,
        "max_retries": 3,
        "timeout": 60,
    },
}


def ensure_config_dir():
    # 确保配置目录存在
    os.makedirs(CONFIG_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    log_debug(f"配置文件: {CONFIG_JSON}")


def _migrate_from_ini():
    # 从旧的 Setup.ini 迁移数据到 config.json
    if not os.path.exists(SETUP_INI):
        return None
    try:
        parser = configparser.ConfigParser()
        parser.read(SETUP_INI, encoding="utf-8")
        migrated: dict = {}
        for section in parser.sections():
            key = section.lower()
            migrated[key] = dict(parser.items(section))
        log_info("已从 Setup.ini 迁移配置")
        return migrated
    except Exception as e:
        log_debug(f"迁移 Setup.ini 失败: {e}")
        return None


def _load_json() -> dict:
    # 加载 config.json
    if os.path.exists(CONFIG_JSON):
        try:
            with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log_warn(f"加载 config.json 失败: {e}")
    return {}


def _save_json(data: dict):
    # 保存 config.json
    try:
        ensure_config_dir()
        with open(CONFIG_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log_error(f"保存 config.json 失败: {e}")


class ConfigManager:
    # 配置管理器 - 读取/写入 CML/config.json

    def __init__(self):
        ensure_config_dir()
        self._data: dict = {}
        self._load()

    def _load(self):
        # 加载配置文件（合并默认值）
        self._data = {}
        for section, options in DEFAULT_CONFIG.items():
            self._data[section] = dict(options)

        # 尝试迁移旧 Setup.ini
        ini_data = _migrate_from_ini()
        if ini_data:
            for section, options in ini_data.items():
                if section in self._data:
                    self._data[section].update(options)

        # 加载 config.json（覆盖默认值和旧配置）
        json_data = _load_json()
        for section, options in json_data.items():
            sec = section.lower()
            if sec in self._data and isinstance(options, dict):
                self._data[sec].update(options)
            elif isinstance(options, dict):
                self._data[sec] = dict(options)

        # 确保关键值合法
        dl = self._data.get("download", {})
        dl["max_threads"] = max(int(dl.get("max_threads", 32)), 1)
        raw_retries = int(dl.get("max_retries", 3))
        dl["max_retries"] = raw_retries if raw_retries >= 0 else 0
        dl["timeout"] = max(int(dl.get("timeout", 60)), 5)

        log_debug(f"已加载配置: {CONFIG_JSON}")
        self.save()

    def save(self):
        # 保存配置到 config.json
        _save_json(self._data)

    # ---- 通用配置接口（保持兼容） ----

    def get(self, section: str, key: str, fallback: str = "") -> str:
        # 获取配置项（字符串）
        try:
            val = self._data[section.lower()][key]
            return str(val) if val is not None else fallback
        except KeyError:
            return fallback

    def set(self, section: str, key: str, value: str):
        # 设置配置项
        sec = section.lower()
        if sec not in self._data:
            self._data[sec] = {}
        self._data[sec][key] = value
        self.save()

    def get_int(self, section: str, key: str, fallback: int = 0) -> int:
        # 获取整数配置项
        try:
            return int(self._data[section.lower()][key])
        except (KeyError, ValueError, TypeError):
            return fallback

    def get_bool(self, section: str, key: str, fallback: bool = False) -> bool:
        # 获取布尔配置项
        try:
            val = str(self._data[section.lower()][key]).lower()
            if val in ("true", "1", "yes"):
                return True
            if val in ("false", "0", "no"):
                return False
        except KeyError:
            pass
        return fallback

    # ---- 下载配置接口 ----

    def get_download_config(self) -> dict:
        # 获取下载配置
        return dict(self._data.get("download", DEFAULT_CONFIG["download"]))

    def set_download_config(self, **kwargs):
        # 设置下载配置项
        if "download" not in self._data:
            self._data["download"] = dict(DEFAULT_CONFIG["download"])
        self._data["download"].update(kwargs)
        self.save()


class AccountManager:
    # 账号管理器

    def __init__(self):
        ensure_config_dir()
        self.accounts = []
        self._load()

    def _load(self):
        # 加载账号列表
        if os.path.exists(ACCOUNTS_FILE):
            try:
                with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.accounts = data.get("accounts", [])
                log_debug(f"已加载 {len(self.accounts)} 个账号")
            except Exception as e:
                log_warn(f"加载账号失败: {e}")
                self.accounts = []

    def save(self):
        # 保存账号列表
        try:
            ensure_config_dir()
            with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"accounts": self.accounts}, f, indent=2, ensure_ascii=False)
            log_debug(f"账号已保存")
        except Exception as e:
            log_error(f"保存账号失败: {e}")

    def add_account(self, account: dict):
        # 添加账号
        # 检查是否已存在相同 UUID 的账号
        for i, acc in enumerate(self.accounts):
            if acc.get("uuid") == account.get("uuid"):
                self.accounts[i] = account
                self.save()
                return
        self.accounts.append(account)
        self.save()

    def remove_account(self, index: int) -> bool:
        # 删除账号
        if 0 <= index < len(self.accounts):
            removed = self.accounts.pop(index)
            self.save()
            log_info(f"已删除账号: {removed.get('name', '未知')}")
            return True
        return False

    def get_account(self, index: int) -> dict | None:
        # 获取指定账号
        if 0 <= index < len(self.accounts):
            return self.accounts[index]
        return None

    def list_accounts(self) -> list[dict]:
        # 列出所有账号
        return self.accounts

    def get_active_account(self) -> dict | None:
        # 获取当前选中的账号
        for acc in self.accounts:
            if acc.get("active", False):
                return acc
        # 如果没有活跃账号，返回第一个
        if self.accounts:
            return self.accounts[0]
        return None

    def set_active(self, index: int) -> bool:
        # 设置活跃账号
        for i, acc in enumerate(self.accounts):
            acc["active"] = (i == index)
        self.save()
        return True
