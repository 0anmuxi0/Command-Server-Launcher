# Minecraft 启动模块
#
# 启动流程:
#   1. 预检测 (Precheck)
#   2. 获取 Java (Java)
#   3. 登录 (Login)
#   4. 补全文件 (Fix)
#   5. 获取启动参数 (Argument)
#   6. 解压 Natives (Natives)
#   7. 启动进程 (Run)
#   8. 等待 (Wait)

import os
import json
import shutil
import subprocess
import zipfile
import uuid as uuid_module
from .logger import (log_info, log_debug, log_warn, log_error, log_success, log_request,
                      COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_WHITE, COLOR_GRAY, RESET)
from .network import net_download, BMCLAPI_MIRROR
from .minecraft import (MinecraftFolder, MinecraftInstance, detect_os,
                         detect_arch, _check_rules)


def _print_colored_game_output(line: str):
    """按日志等级给游戏输出着色，只带颜色、不附加 [时间] [等级] 前缀。"""
    stripped = line.rstrip()
    if not stripped:
        return
    if ("[ERROR]" in stripped or "/ERROR]" in stripped or " ERROR:" in stripped
        or " FATAL" in stripped or "Exception in thread" in stripped):
        color = COLOR_RED
    elif ("[WARN]" in stripped or "/WARN]" in stripped or " WARN: " in stripped
          or " WARNING:" in stripped):
        color = COLOR_YELLOW
    elif "[SUCCESS]" in stripped:
        color = COLOR_GREEN
    elif "[DEBUG]" in stripped or "/DEBUG]" in stripped or "[TRACE]" in stripped:
        color = COLOR_GRAY
    else:
        color = COLOR_WHITE
    print(f"{color}{stripped}{RESET}")


class LaunchOptions:
    """启动选项"""
    
    def __init__(self):
        self.server_ip: str = ""           # 强制进服
        self.extra_game_args: str = ""     # 额外游戏参数
        self.instance: MinecraftInstance | None = None  # MinecraftInstance
        self.account: dict | None = None    # 登录结果 dict
        self.java_path: str = ""           # Java 路径
        self.min_memory: int = 1024        # 最小内存 MB
        self.max_memory: int = 2048        # 最大内存 MB
        self.jvm_args: str = ""            # 额外 JVM 参数
        self.window_width: int = 854       # 窗口宽度
        self.window_height: int = 480      # 窗口高度
        self.version_isolation: bool = False  # 版本隔离


def _get_java_major_version(java_path: str) -> int:
    # 获取 Java 主版本号
    try:
        result = subprocess.run(
            [java_path, "-version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            return 0
        # Java 版本输出格式: openjdk version "21.0.2" 或 java version "1.8.0_302"
        import re
        match = re.search(r'"(\d+)(?:\.(\d+))?', result.stderr)
        if match:
            major = int(match.group(1))
            if major == 1:
                # Java 8 显示为 1.8，取第二个数字
                return int(match.group(2)) if match.group(2) else 8
            return major
        return 0
    except Exception:
        return 0


def find_java(min_version: int = 8) -> str:
    # 自动查找 Java，min_version 为最低主版本号
    found: list[tuple[str, int]] = []

    # 1. 从 PATH 环境变量中搜索
    path_env = os.environ.get("PATH", "")
    seen = set()
    for p in path_env.split(";"):
        p = p.strip().strip('"')
        if not p:
            continue
        for exe in ("java.exe", "javaw.exe"):
            full = os.path.join(p, exe)
            if os.path.isfile(full) and full not in seen:
                seen.add(full)
                ver = _get_java_major_version(full)
                if ver >= min_version:
                    found.append((full, ver))

    # 2. 常见安装路径（已知版本，免检测）
    known_paths = [
        (r"C:\Program Files\Eclipse Adoptium\jdk-21.0.2.13-hotspot\bin\java.exe", 21),
        (r"C:\Program Files\Eclipse Adoptium\jdk-21\bin\java.exe", 21),
        (r"C:\Program Files\Eclipse Adoptium\jdk-17\bin\java.exe", 17),
        (r"C:\Program Files\Java\jdk-21\bin\java.exe", 21),
        (r"C:\Program Files\Java\jdk-17\bin\java.exe", 17),
        (r"C:\Program Files\BellSoft\*\bin\java.exe", 0),   # Liberica JDK
        (r"C:\Program Files\Zulu\*\bin\java.exe", 0),
        (r"C:\Program Files\AdoptOpenJDK\*\bin\java.exe", 0),
    ]
    pattern_dirs = []
    for path, ver in known_paths:
        if "*" in path:
            # 带通配符的路径，用 glob
            import glob
            matches = glob.glob(path)
            for m in matches:
                if os.path.isfile(m) and m not in seen:
                    seen.add(m)
                    v = _get_java_major_version(m)
                    if v >= min_version:
                        found.append((m, v))
        else:
            if os.path.exists(path) and path not in seen:
                seen.add(path)
                if ver >= min_version:
                    found.append((path, ver))

    # 3. 搜索常见安装目录
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    search_roots = [
        r"C:\Program Files\Eclipse Adoptium",
        r"C:\Program Files\Java",
        r"C:\Program Files (x86)\Java",
        r"C:\Program Files\BellSoft",
        r"C:\Program Files\Zulu",
        r"C:\Program Files\AdoptOpenJDK",
        r"C:\Program Files\Microsoft",
    ]
    if local_appdata:
        search_roots.append(os.path.join(local_appdata, "Programs"))
    for root in search_roots:
        if os.path.exists(root):
            try:
                for dirpath, _, filenames in os.walk(root):
                    for exe in ("java.exe", "javaw.exe"):
                        if exe in filenames:
                            full_path = os.path.join(dirpath, exe)
                            if full_path not in seen:
                                seen.add(full_path)
                                ver = _get_java_major_version(full_path)
                                if ver >= min_version:
                                    found.append((full_path, ver))
            except Exception:
                continue

    if found:
        # 按与所需版本的差距从小到大排序，同差距优先选短路径
        found.sort(key=lambda x: (abs(x[1] - min_version), len(x[0])))
        best = found[0]
        log_info(f"找到 Java {best[1]}: {best[0]}")
        return best[0]

    log_warn(f"未找到 Java {min_version}+，请手动指定路径")
    return ""


def generate_launch_arguments(options: LaunchOptions) -> list[str] | None:
    # 生成启动参数
    #
    # 返回 JVM 参数列表
    instance = options.instance
    if not instance:
        log_error("未指定版本实例")
        return None
    
    folder = instance.folder
    mc_location = folder.location
    
    if options.version_isolation:
        mc_location = os.path.join(folder.location, "versions", instance.name)
        os.makedirs(mc_location, exist_ok=True)
    
    account = options.account or {
        "type": "legacy",
        "name": "Player",
        "uuid": str(uuid_module.uuid4()),
        "access_token": str(uuid_module.uuid4()),
    }
    
    # 基础参数
    args = []
    
    # 内存参数
    args.append(f"-Xms{options.min_memory}M")
    args.append(f"-Xmx{options.max_memory}M")
    
    # 额外 JVM 参数
    if options.jvm_args:
        args.extend(options.jvm_args.split())
    
    # 来自 version.json 的 JVM 参数
    jvm_args = instance.get_arguments()
    skip_next = False
    for arg in jvm_args:
        if skip_next:
            skip_next = False
            continue
        if isinstance(arg, str):
            # version.json 中 -cp 后紧跟 ${classpath}，跳过这对
            if arg == "-cp":
                skip_next = True
                continue
            
            # 替换变量
            arg = arg.replace("${natives_directory}", instance.natives_dir)
            arg = arg.replace("${launcher_name}", "CML-Minecraft-Launcher")
            arg = arg.replace("${launcher_version}", "1.0.0")
            arg = arg.replace("${classpath_separator}", ";")
            arg = arg.replace("${library_directory}", folder.libraries_dir)
            arg = arg.replace("${version_name}", instance.name)
            arg = arg.replace("${assets_root}", folder.assets_dir)
            
            # 一些参数可能有规则
            if "${classpath}" in arg:
                # 后面会处理 classpath
                continue
            
            args.append(arg)
    
    # Classpath
    classpath = _build_classpath(instance, folder)
    args.append("-cp")
    args.append(classpath)
    
    # Main class
    args.append(instance.get_main_class())
    
    # 游戏参数
    game_args = instance.get_game_arguments()
    
    # 替换游戏参数
    if game_args:
        game_args = game_args.replace("${auth_player_name}", account.get("name", "Player"))
        game_args = game_args.replace("${auth_session}", account.get("access_token", ""))
        game_args = game_args.replace("${auth_uuid}", account.get("uuid", ""))
        game_args = game_args.replace("${auth_access_token}", account.get("access_token", ""))
        game_args = game_args.replace("${auth_xuid}", account.get("xuid", ""))
        game_args = game_args.replace("${user_type}", "msa" if account.get("type") == "microsoft" else "legacy")
        game_args = game_args.replace("${version_name}", instance.name)
        game_args = game_args.replace("${game_directory}", mc_location)
        game_args = game_args.replace("${assets_root}", folder.assets_dir)
        game_args = game_args.replace("${assets_index_name}", instance.get_asset_index())
        game_args = game_args.replace("${game_assets}", folder.assets_dir)
        
        args.extend(game_args.split())
    
    # 额外游戏参数
    if options.extra_game_args:
        args.extend(options.extra_game_args.split())
    
    # 服务器地址
    if options.server_ip:
        server_parts = options.server_ip.split(":")
        if len(server_parts) >= 1:
            args.append("--server")
            args.append(server_parts[0])
            if len(server_parts) >= 2:
                args.append("--port")
                args.append(server_parts[1])
    
    # 窗口大小
    args.append("--width")
    args.append(str(options.window_width))
    args.append("--height")
    args.append(str(options.window_height))
    
    return args


def _build_classpath(instance: MinecraftInstance, folder: MinecraftFolder) -> str:
    # 构建 Classpath
    #
    # 遍历所有 libraries 构建 classpath
    classpath_parts = []
    seen_paths: set[str] = set()

    # 添加 client.jar（如果实例自身有就用自身的，否则检查继承父版本）
    if os.path.exists(instance.jar_path):
        classpath_parts.append(instance.jar_path)
        seen_paths.add(os.path.normcase(os.path.realpath(instance.jar_path)))
    elif instance.json_object and instance.json_object.get("inheritsFrom"):
        parent_name = instance.json_object["inheritsFrom"]
        parent_jar = os.path.join(folder.versions_dir, parent_name, f"{parent_name}.jar")
        if os.path.exists(parent_jar):
            classpath_parts.append(parent_jar)
            seen_paths.add(os.path.normcase(os.path.realpath(parent_jar)))

    # 添加 libraries（去重）
    libraries = instance.get_libraries()
    for lib in libraries:
        # 检查规则
        rules = lib.get("rules", [])
        if not _check_rules(rules):
            continue

        # 获取 artifact
        downloads = lib.get("downloads", {})
        artifact = downloads.get("artifact", {})
        lib_path = artifact.get("path", "")

        # 处理 natives
        classifiers = downloads.get("classifiers", {})
        if classifiers:
            os_name = detect_os()
            native_key = None
            for key in classifiers:
                if os_name in key:
                    native_key = key
                    break

            if native_key and native_key != f"natives-{os_name}":
                # natives 会在后面解压，不加入 classpath
                continue

        if lib_path:
            full_path = os.path.join(folder.libraries_dir, lib_path)
            if os.path.exists(full_path):
                # 去重：相同 realpath 的不重复添加
                real = os.path.normcase(os.path.realpath(full_path))
                if real not in seen_paths:
                    seen_paths.add(real)
                    classpath_parts.append(full_path)
            else:
                # 尝试从 name 解析
                lib_name = lib.get("name", "")
                if lib_name:
                    resolved = _resolve_library_path(lib_name, folder)
                    if resolved:
                        classpath_parts.append(resolved)
    
    return ";".join(classpath_parts)


def _resolve_library_path(lib_name: str, folder: MinecraftFolder) -> str | None:
    # 从 Maven 坐标解析库路径
    parts = lib_name.split(":")
    if len(parts) < 3:
        return None
    
    group = parts[0].replace(".", "/")
    artifact = parts[1]
    version = parts[2]
    
    # 处理扩展名
    ext = "jar"
    if len(parts) > 3:
        classifier_parts = parts[3].split("@")
        if len(classifier_parts) > 1:
            ext = classifier_parts[1]
    
    lib_path = os.path.join(group, artifact, version, f"{artifact}-{version}.{ext}")
    full_path = os.path.join(folder.libraries_dir, lib_path)
    
    if os.path.exists(full_path):
        return full_path
    return None


def extract_natives(instance: MinecraftInstance, folder: MinecraftFolder) -> bool:
    # 解压 Native 库
    natives_dir = instance.natives_dir
    
    # 清理旧的 natives
    if os.path.exists(natives_dir):
        shutil.rmtree(natives_dir, ignore_errors=True)
    os.makedirs(natives_dir, exist_ok=True)
    
    os_name = detect_os()
    
    libraries = instance.get_libraries()
    extracted = 0
    
    for lib in libraries:
        rules = lib.get("rules", [])
        if not _check_rules(rules):
            continue
        
        downloads = lib.get("downloads", {})
        classifiers = downloads.get("classifiers", {})
        
        # 查找匹配的 natives
        native_key = None
        for key in classifiers:
            if os_name in key:
                native_key = key
                break
        
        if not native_key:
            continue
        
        native_info = classifiers[native_key]
        native_path = native_info.get("path", "")
        
        if not native_path:
            continue
        
        native_full_path = os.path.join(folder.libraries_dir, native_path)
        
        if os.path.exists(native_full_path):
            try:
                with zipfile.ZipFile(native_full_path, "r") as zf:
                    # 只提取 .dll/.so/.dylib 文件
                    for entry in zf.namelist():
                        if (entry.endswith(".dll") or entry.endswith(".so") or
                            entry.endswith(".dylib") or entry.endswith(".jnilib")):
                            # 避免路径穿越
                            safe_name = os.path.basename(entry)
                            if safe_name:
                                zf.extract(entry, natives_dir)
                                extracted += 1
            except Exception as e:
                log_debug(f"解压 natives {native_path} 失败: {e}")
    
    log_info(f"已解压 {extracted} 个 Native 文件")
    return True


def launch_minecraft(options: LaunchOptions) -> bool:
    # 启动 Minecraft
    #
    # 完整启动流程
    if not options.instance:
        log_error("未指定要启动的版本")
        return False
    
    instance = options.instance
    log_info(f"准备启动 Minecraft {instance.name}...")
    log_info(f"  版本类型: {instance.get_state_name()}")
    
    # 步骤 1: 预检测
    log_info("[1/7] 预检测...")
    if instance.state == -1:
        log_error("版本状态异常，无法启动")
        return False
    
    if not options.account:
        log_warn("未登录账号，使用离线模式")
        import uuid
        options.account = {
            "type": "legacy",
            "name": "Player",
            "uuid": str(uuid.uuid4()),
            "access_token": str(uuid.uuid4()),
        }
    
    # 步骤 2: 获取 Java
    log_info("[2/7] 获取 Java...")
    needed = instance.get_java_version()
    java_path = options.java_path or ""
    # 验证已配置的 Java 版本是否达标
    if java_path:
        detected = _get_java_major_version(java_path)
        if detected < needed:
            log_warn(f"配置的 Java {detected} 不满足需求 (需要 Java {needed})，重新搜索...")
            java_path = ""
    if not java_path:
        java_path = find_java(min_version=needed)
    if not java_path:
        log_error("未找到 Java，请先在设置中配置 Java 路径")
        return False
    
    log_info(f"  Java: {java_path}")
    
    # 步骤 3: 登录（已登录则跳过）
    log_info("[3/7] 登录验证...")
    if options.account.get("type") == "microsoft":
        log_info(f"  使用微软账号: {options.account.get('name', '')}")
    else:
        log_info(f"  使用离线账号: {options.account.get('name', '')}")
    
    # 步骤 4: 补全文件
    log_info("[4/7] 补全文件...")
    # 检查必要的文件
    if not os.path.exists(instance.jar_path):
        log_error(f"  client.jar 不存在: {instance.jar_path}")
        log_error("  请先使用安装功能下载 Minecraft 版本")
        return False
    
    # 步骤 5: 解压 Natives
    log_info("[5/7] 解压 Native 库...")
    extract_natives(instance, instance.folder)
    
    # 步骤 6: 生成启动参数
    log_info("[6/7] 生成启动参数...")
    args = generate_launch_arguments(options)
    if not args:
        log_error("生成启动参数失败")
        return False
    
    log_debug(f"  启动参数: {' '.join(args[:20])}...")
    
    # 步骤 7: 启动进程
    log_info("[7/7] 启动 Minecraft...")
    log_success("正在启动游戏...")
    
    try:
        # 设置环境变量和工作目录
        env = os.environ.copy()
        env["APPDATA"] = instance.folder.location

        mc_location = instance.folder.location
        if options.version_isolation:
            mc_location = os.path.join(instance.folder.location, "versions", instance.name)
            os.makedirs(mc_location, exist_ok=True)

        # Minecraft 日志固定为 UTF-8 编码，强制使用 utf-8 解码避免中文乱码
        system_encoding = "utf-8"

        process = subprocess.Popen(
            [java_path] + args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=mc_location,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            encoding=system_encoding,
            errors="replace",
            bufsize=1
        )
        
        log_success(f"Minecraft 已启动! (PID: {process.pid})")

        # 输出游戏日志（按关键字着色）
        if process.stdout:
            for line in process.stdout:
                _print_colored_game_output(line)
        
        process.wait()
        
        if(process.returncode == 0):
            log_success("Minecraft 已正常退出")
        else:
            log_error(f"Minecraft 异常退出 ({process.returncode})")
        
        return process.returncode == 0
        
    except FileNotFoundError:
        log_error(f"找不到 Java: {java_path}")
        return False
    except Exception as e:
        log_error(f"启动失败: {e}")
        return False
