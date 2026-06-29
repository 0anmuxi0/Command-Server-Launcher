# 日志系统

import sys
import os
import time
from datetime import datetime

# 颜色转义码（不含标签，用于原始输出）
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_WHITE = "\033[37m"
COLOR_GRAY = "\033[90m"
COLOR_CYAN = "\033[96m"

# 在 Windows 上启用 ANSI 转义支持
if os.name == "nt":
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
    except Exception:
        pass

INFO = COLOR_WHITE + "[INFO]"      # 白色
DEBUG = COLOR_GRAY + "[DEBUG]"    # 灰色
ERROR = COLOR_RED + "[ERROR]"    # 红色
WARN = COLOR_YELLOW + "[WARN]"      # 黄色
REQUEST = COLOR_CYAN + "[REQUEST]"  # 青色
SUCCESS = COLOR_GREEN + "[SUCCESS]"  # 绿色
RESET = "\033[0m"

# 日志等级
LOG_LEVEL_DEBUG = 0
LOG_LEVEL_INFO = 1
LOG_LEVEL_WARN = 2
LOG_LEVEL_ERROR = 3

_current_log_level = LOG_LEVEL_INFO


def set_log_level(level: int):
    # 设置日志等级
    global _current_log_level
    _current_log_level = level


def _get_module_name() -> str:
    """从调用栈自动获取调用模块名（不含路径和 .py）。"""
    try:
        import inspect
        frame = inspect.currentframe()
        while frame is not None:
            frame = frame.f_back
            filename = frame.f_code.co_filename if frame else ""
            basename = os.path.basename(filename) if filename else ""
            name, _ = os.path.splitext(basename) if basename else ("", "")
            if name and name != "logger":
                return name
    except Exception:
        pass
    return ""


def _format_tag(tag: str, timestamp: str, module: str = "") -> str:
    # 从标签常量提取颜色和名称，组装成 [时间] [模块/标签] 格式
    # tag 例如 "\033[37m[INFO]"
    # 注意: ANSI 转义序列 \033[37m 中也含 [，所以用 rfind 找最后一个 [
    pos = tag.rfind("[")
    color = tag[:pos]           # "\033[37m"
    name = tag[pos + 1:-1]      # "INFO"
    if module:
        return f"{color}[{timestamp}] [{module}/{name}]"
    return f"{color}[{timestamp}] [{name}]"


def _log(tag: str, *args, level: int = LOG_LEVEL_INFO, module: str = ""):
    # 内部日志输出
    if level < _current_log_level:
        return
    if not module:
        module = _get_module_name()
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = " ".join(str(arg) for arg in args)
    print(f"{_format_tag(tag, timestamp, module)}: {message}{RESET}")


def log_info(*args, module: str = ""):
    # 输出 [INFO] 日志
    _log(INFO, *args, level=LOG_LEVEL_INFO, module=module)


def log_debug(*args, module: str = ""):
    # 输出 [DEBUG] 日志
    _log(DEBUG, *args, level=LOG_LEVEL_DEBUG, module=module)


def log_warn(*args, module: str = ""):
    # 输出 [WARN] 日志
    _log(WARN, *args, level=LOG_LEVEL_WARN, module=module)


def log_error(*args, module: str = ""):
    # 输出 [ERROR] 日志
    _log(ERROR, *args, level=LOG_LEVEL_ERROR, module=module)


def log_success(*args, module: str = ""):
    # 输出 [SUCCESS] 日志
    _log(SUCCESS, *args, level=LOG_LEVEL_INFO, module=module)


def log_request(*args, module: str = ""):
    # 输出 [REQUEST] 日志
    _log(REQUEST, *args, level=LOG_LEVEL_INFO, module=module)


def log_input(prompt: str, module: str = "") -> str:
    """打印带时间戳和模块名的 [REQUEST] 提示，然后返回 input() 结果。"""
    if not module:
        module = _get_module_name()
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = _format_tag(REQUEST, timestamp, module)
    return input(f"{formatted} {prompt}")
