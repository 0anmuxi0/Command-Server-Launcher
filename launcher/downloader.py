# 多线程下载模块
# 支持智能线程分配、进度输出
# 配置通过 ConfigManager 统一管理（CML/config.json）

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests
import urllib3
from .logger import log_info, log_debug, log_warn, log_error, log_success
from .config import ConfigManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_config_mgr: ConfigManager | None = None


def init(config: ConfigManager):
    """初始化下载模块，传入 ConfigManager 实例"""
    global _config_mgr
    _config_mgr = config


def _get_dl_config() -> dict:
    """获取下载配置"""
    if _config_mgr:
        return _config_mgr.get_download_config()
    return {"max_threads": 0, "max_retries": 3, "timeout": 30}


def get_max_threads() -> int:
    """获取线程数"""
    cfg = _get_dl_config()
    return max(cfg.get("max_threads", 32), 1)


@dataclass
class DownloadTask:
    """单个下载任务"""
    url: str
    save_path: str
    name: str = ""          # 显示名称
    size: int = 0           # 文件大小（字节）


class _ProgressTracker:
    """下载进度跟踪器（仅计数，不输出聚合进度）"""
    def __init__(self, total: int, desc: str = "下载"):
        self.lock = threading.Lock()
        self.total = total
        self.desc = desc
        self.completed = 0
        self.failed = 0
        self.errors: list[str] = []

    def add_success(self):
        with self.lock:
            self.completed += 1

    def add_failed(self, name: str, error: str):
        with self.lock:
            self.failed += 1
            self.errors.append(f"[{name}] {error}")

    def finish(self):
        if self.errors:
            log_info(f"下载完成: 成功 {self.completed}, 失败 {self.failed}")
            for err in self.errors:
                log_error(f"下载失败: {err}")


def _report_download_progress(filename: str, downloaded: int, total_bytes: int, last_pct: list[int] | None = None):
    """输出统一格式的下载进度"""
    if total_bytes > 0:
        pct = min(100, downloaded * 100 // total_bytes)
        if last_pct is not None and pct == last_pct[0]:
            return
        if last_pct is not None:
            last_pct[0] = pct
        mb_dl = downloaded / (1024 * 1024)
        mb_total = total_bytes / (1024 * 1024)
        log_info(f"{filename}: {mb_dl:.1f}MB/{mb_total:.1f}MB ({pct}%)")
    else:
        mb_dl = downloaded / (1024 * 1024)
        log_info(f"{filename}: 已下载 {mb_dl:.1f}MB")


def _create_session() -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.headers.update({"User-Agent": "CML-Python-Launcher/1.0.0"})
    return session


def _is_permanent_http_error(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError):
        response = getattr(exc, "response", None)
        if response is not None and 400 <= response.status_code < 500:
            return True
    return False


def _download_range(session: requests.Session, url: str, save_path: str,
                    start: int, end: int, timeout: int,
                    filename: str, downloaded_bytes: list[int],
                    download_lock: threading.Lock, total_bytes: int,
                    last_pct: list[int], error_holder: list[Exception | None]) -> bool:
    headers = {"Range": f"bytes={start}-{end}"}
    chunk_count = 0
    try:
        with session.get(url, headers=headers, stream=True, timeout=timeout) as resp:
            resp.raise_for_status()
            with open(save_path, "r+b") as f:
                pos = start
                for chunk in resp.iter_content(65536):
                    if not chunk:
                        break
                    with download_lock:
                        f.seek(pos)
                        f.write(chunk)
                        pos += len(chunk)
                        chunk_count += len(chunk)
                        downloaded_bytes[0] += len(chunk)
                        _report_download_progress(filename, downloaded_bytes[0], total_bytes, last_pct)
        return True
    except Exception as e:
        with download_lock:
            downloaded_bytes[0] -= chunk_count
        if _is_permanent_http_error(e):
            with download_lock:
                if error_holder[0] is None:
                    error_holder[0] = e
            log_debug(f"{filename} 下载分片永久失败: {e}")
        else:
            log_debug(f"{filename} 下载分片异常: {e}")
        return False


def _do_single(task: DownloadTask, timeout: int,
               progress: _ProgressTracker | None) -> bool:
    cfg = _get_dl_config()
    max_retries = int(cfg.get("max_retries", 3))
    attempt = 0
    while max_retries == 0 or attempt < max_retries:
        attempt += 1
        try:
            os.makedirs(os.path.dirname(task.save_path), exist_ok=True)
            filename = task.name or os.path.basename(task.save_path)

            # 先 HEAD 请求获取文件大小
            session = _create_session()
            head_resp = session.head(task.url, allow_redirects=True, timeout=timeout)
            head_resp.raise_for_status()
            total_bytes = int(head_resp.headers.get("Content-Length", 0))
            accept_ranges = head_resp.headers.get("Accept-Ranges", "").lower() == "bytes"

            tmp_path = task.save_path + ".tmp"
            downloaded_ok = False

            # 尝试分段下载（支持 Range 且大于 2MB 时）
            if accept_ranges and total_bytes > 1024 * 1024 * 2:
                try:
                    thread_count = get_max_threads()
                    chunk_size = max(total_bytes // thread_count, 1024 * 1024)
                    ranges: list[tuple[int, int]] = []
                    for start in range(0, total_bytes, chunk_size):
                        end = min(start + chunk_size - 1, total_bytes - 1)
                        ranges.append((start, end))

                    with open(tmp_path, "wb") as f:
                        f.truncate(total_bytes)

                    part_ok = [False] * len(ranges)
                    progress_lock = threading.Lock()
                    downloaded_bytes = [0]
                    last_pct = [0]

                    error_holder: list[Exception | None] = [None]

                    def _dl_part(idx: int, start: int, end: int):
                        part_retries = max(max_retries, 3) if max_retries > 0 else 3
                        for part_attempt in range(part_retries):
                            if error_holder[0] is not None:
                                return
                            part_session = _create_session()
                            try:
                                if _download_range(part_session, task.url, tmp_path, start, end,
                                                   timeout, filename, downloaded_bytes,
                                                   progress_lock, total_bytes, last_pct, error_holder):
                                    part_ok[idx] = True
                                    return
                            finally:
                                part_session.close()

                            if error_holder[0] is not None:
                                return

                            log_warn(f"{filename} 分片 {start}-{end} 下载失败，第{part_attempt + 1}次重试")
                            if part_attempt < part_retries - 1:
                                time.sleep(1)

                    threads = []
                    for i, (s, e) in enumerate(ranges):
                        t = threading.Thread(target=_dl_part, args=(i, s, e))
                        t.start()
                        threads.append(t)
                    for t in threads:
                        t.join()

                    if all(part_ok):
                        downloaded_ok = True
                    else:
                        log_warn(f"{filename} 分段下载失败，回退为普通下载")
                except Exception as e:
                    if _is_permanent_http_error(e):
                        raise
                    log_warn(f"{filename} 分段下载异常: {e}")

            # 未走分段 或 分段失败 → 普通下载
            if not downloaded_ok:
                with _create_session().get(task.url, stream=True, timeout=timeout) as resp:
                    resp.raise_for_status()
                    downloaded = 0
                    last_pct = [0]
                    with open(tmp_path, "wb") as f:
                        for chunk in resp.iter_content(65536):
                            if not chunk:
                                break
                            f.write(chunk)
                            downloaded += len(chunk)
                            if total_bytes > 0:
                                _report_download_progress(filename, downloaded, total_bytes, last_pct)

            if os.path.exists(task.save_path):
                os.remove(task.save_path)
            os.rename(tmp_path, task.save_path)
            log_success(f"成功下载: {filename}")

            if progress:
                progress.add_success()
            session.close()
            return True

        except Exception as e:
            for f_path in [task.save_path + ".tmp", task.save_path + ".parts"]:
                if os.path.exists(f_path):
                    try:
                        if os.path.isdir(f_path):
                            import shutil
                            shutil.rmtree(f_path, ignore_errors=True)
                        else:
                            os.remove(f_path)
                    except Exception:
                        pass

            name = task.name or os.path.basename(task.save_path)
            log_warn(f"{name} 第{attempt}次重试: {e}")
            # 如果是 4xx 永久错误，则不再重试
            if _is_permanent_http_error(e):
                log_error(f"{name} 遇到永久错误，不再重试: {e}")
                break
            if attempt < max_retries:
                time.sleep(1)
            try:
                session.close()
            except Exception:
                pass

    return False


def download_multi(tasks: list[DownloadTask],
                   max_threads: int | None = None,
                   timeout: int | None = None,
                   desc: str = "下载") -> tuple[int, int]:
    if not tasks:
        return 0, 0

    cfg = _get_dl_config()
    if max_threads is None:
        max_threads = get_max_threads()

    if timeout is None:
        timeout_value = cfg.get("timeout", 30)
        timeout = int(timeout_value) if timeout_value is not None else 30
    else:
        timeout = int(timeout)

    max_threads = int(max_threads)

    total = len(tasks)
    progress = _ProgressTracker(total, desc)

    log_info(f"开始 {desc}: {total} 个文件")

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {
            executor.submit(_do_single, task, timeout, progress): task
            for task in tasks
        }
        for future in as_completed(futures):
            task = futures[future]
            try:
                if not future.result():
                    progress.add_failed(task.name or os.path.basename(task.save_path), "下载失败")
            except Exception as e:
                progress.add_failed(task.name or os.path.basename(task.save_path), str(e))

    progress.finish()
    return progress.completed, progress.failed


def download_single(url: str, save_path: str,
                    timeout: int = 30) -> bool:
    task = DownloadTask(url=url, save_path=save_path,
                        name=os.path.basename(save_path))
    return _do_single(task, timeout, None)
