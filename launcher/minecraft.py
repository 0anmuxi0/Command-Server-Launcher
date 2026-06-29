# Minecraft 版本管理

import os
import json
import re
from .logger import log_info, log_debug, log_warn, log_error, log_success
from .network import net_request, net_download, net_get_manifest, BMCLAPI_MIRROR
from .downloader import download_multi, DownloadTask

# Minecraft 版本状态
INSTANCE_STATE_ERROR = -1
INSTANCE_STATE_ORIGINAL = 0
INSTANCE_STATE_SNAPSHOT = 1
INSTANCE_STATE_FOOL = 2
INSTANCE_STATE_OPTIFINE = 3
INSTANCE_STATE_OLD = 4
INSTANCE_STATE_FORGE = 5
INSTANCE_STATE_NEOFORGE = 6
INSTANCE_STATE_LITELOADER = 7
INSTANCE_STATE_FABRIC = 8

# 操作系统检测
IS_WINDOWS = os.name == "nt"


def detect_os() -> str:
    # 检测当前操作系统
    if IS_WINDOWS:
        return "windows"
    import platform
    system = platform.system().lower()
    if system == "darwin":
        return "osx"
    return "linux"


def detect_arch() -> str:
    # 检测系统架构
    import platform
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        return "x86_64" if IS_WINDOWS else "64"
    return "x86" if IS_WINDOWS else "32"


def _check_rules(rules: list[dict] | None) -> bool:
    """检查 Minecraft 规则列表，返回是否允许当前平台加载。"""
    if not rules:
        return True

    current_os = detect_os()
    current_arch = detect_arch()
    allowed = False

    for rule in rules:
        action = rule.get("action", "allow")
        if action not in ("allow", "disallow"):
            continue

        os_rule = rule.get("os")
        if os_rule:
            name = os_rule.get("name", "").lower()
            if name and name != current_os:
                continue

            arch = os_rule.get("arch", "").lower()
            if arch and arch != current_arch:
                continue

            version = os_rule.get("version", "")
            if version:
                # 仅支持简单匹配，默认忽略复杂版本规则
                if version not in current_os:
                    continue

        # 目前不对 features 进行深入判断，默认接受
        allowed = action == "allow"

    return allowed


class MinecraftFolder:
    # Minecraft 文件夹
    
    def __init__(self, name: str, location: str, folder_type: str = "custom"):
        self.name = name
        self.location = os.path.normpath(location)
        self.type = folder_type  # vanila, custom
    
    @property
    def versions_dir(self) -> str:
        return os.path.join(self.location, "versions")
    
    @property
    def assets_dir(self) -> str:
        return os.path.join(self.location, "assets")
    
    @property
    def libraries_dir(self) -> str:
        return os.path.join(self.location, "libraries")
    
    @property
    def mods_dir(self) -> str:
        return os.path.join(self.location, "mods")
    
    @property
    def resourcepacks_dir(self) -> str:
        return os.path.join(self.location, "resourcepacks")
    
    @property
    def saves_dir(self) -> str:
        return os.path.join(self.location, "saves")
    
    @property
    def launcher_profiles_path(self) -> str:
        return os.path.join(self.location, "launcher_profiles.json")
    
    def ensure_dirs(self):
        # 确保所有必要目录存在
        for d in [self.versions_dir, self.assets_dir, self.libraries_dir,
                  self.mods_dir, self.resourcepacks_dir, self.saves_dir]:
            os.makedirs(d, exist_ok=True)
    
    def scan_versions(self) -> list:
        # 扫描 versions 目录下的版本
        versions = []
        versions_path = self.versions_dir
        
        if not os.path.exists(versions_path):
            return versions
        
        for name in os.listdir(versions_path):
            version_dir = os.path.join(versions_path, name)
            if os.path.isdir(version_dir):
                # 检查是否有 JSON 文件
                json_path = os.path.join(version_dir, f"{name}.json")
                jar_path = os.path.join(version_dir, f"{name}.jar")
                if os.path.exists(json_path):
                    versions.append(MinecraftInstance(name, self))
        
        return versions
    
    def __repr__(self):
        return f"McFolder({self.name}, {self.location})"


class MinecraftInstance:
    # Minecraft 版本实例
    
    def __init__(self, name: str, folder: MinecraftFolder):
        self.name = name
        self.folder = folder
        self.path_version = os.path.join(folder.versions_dir, name)
        self.path_indie = folder.location  # 版本隔离时改变
        
        # JSON 数据
        self.json_object = None
        self.json_text = ""
        
        # 版本信息
        self.state = INSTANCE_STATE_ORIGINAL
        self.version = MinecraftVersion()
        self.release_time = ""
        self.info = ""
        self.is_star = False
        
        # 加载版本信息
        self._load_json()
    
    @property
    def json_path(self) -> str:
        return os.path.join(self.path_version, f"{self.name}.json")
    
    @property
    def jar_path(self) -> str:
        return os.path.join(self.path_version, f"{self.name}.jar")
    
    @property
    def natives_dir(self) -> str:
        return os.path.join(self.path_version, "natives")
    
    def _load_json(self):
        # 加载版本 JSON
        if os.path.exists(self.json_path):
            try:
                with open(self.json_path, "r", encoding="utf-8") as f:
                    self.json_text = f.read()
                    self.json_object = json.loads(self.json_text)
                    
                # 处理继承
                self._resolve_inherits()
                
                # 解析版本信息
                self._parse_version_info()
                
            except Exception as e:
                log_error(f"加载版本 {self.name} JSON 失败: {e}")
                self.state = INSTANCE_STATE_ERROR
    
    def _resolve_inherits(self):
        # 解析 JSON 继承链
        #
        # 有些版本（如 Forge）继承自原版版本
        if not self.json_object:
            return
        
        # 处理 patches (HMCL 格式)
        patches = self.json_object.get("patches", [])
        if patches:
            # 合并 patches 到主 JSON
            main_json = None
            for patch in patches:
                if patch.get("jar", ""):
                    main_json = patch
                    break
            if main_json:
                # 合并主 JSON 到 self.json_object
                for key, value in main_json.items():
                    if key not in self.json_object:
                        self.json_object[key] = value
        
        # 处理继承
        inherits_from = self.json_object.get("inheritsFrom", "")
        if not inherits_from:
            return
        
        # 尝试加载父版本 JSON
        parent_path = os.path.join(self.folder.versions_dir, inherits_from, f"{inherits_from}.json")
        if os.path.exists(parent_path):
            try:
                with open(parent_path, "r", encoding="utf-8") as f:
                    parent_json = json.load(f)
                
                # 合并父版本数据到当前版本
                merged = dict(parent_json)
                merged.update(self.json_object)
                
                # 合并 libraries
                libs = parent_json.get("libraries", []) + self.json_object.get("libraries", [])
                merged["libraries"] = libs
                
                # 合并参数
                for key in ["minecraftArguments", "arguments"]:
                    if key in parent_json and key not in self.json_object:
                        merged[key] = parent_json[key]
                
                self.json_object = merged
                
            except Exception as e:
                log_debug(f"加载父版本 {inherits_from} 失败: {e}")
    
    def _parse_version_info(self):
        # 解析版本信息
        if not self.json_object:
            return
        
        # 发布时间
        self.release_time = self.json_object.get("releaseTime", "")
        
        # 版本类型
        type_str = self.json_object.get("type", "")
        if type_str == "release":
            self.state = INSTANCE_STATE_ORIGINAL
        elif type_str == "snapshot":
            self.state = INSTANCE_STATE_SNAPSHOT
        elif type_str == "old_alpha" or type_str == "old_beta":
            self.state = INSTANCE_STATE_OLD
        elif type_str == "fool":
            self.state = INSTANCE_STATE_FOOL
        
        # 检测 Mod Loader
        inherits_from = self.json_object.get("inheritsFrom", "")
        main_class = self.json_object.get("mainClass", "")
        libraries = self.json_object.get("libraries", [])
        
        # 先单独检查 neoforged（优先于 forge，避免继承链中 forge 库误覆盖）
        for lib in libraries:
            if "net.neoforged" in lib.get("name", ""):
                self.state = INSTANCE_STATE_NEOFORGE
                break
        else:
            # 再检查其他加载器
            for lib in libraries:
                lib_name = lib.get("name", "")
                if "net.minecraftforge" in lib_name:
                    self.state = INSTANCE_STATE_FORGE
                elif "net.fabricmc" in lib_name:
                    self.state = INSTANCE_STATE_FABRIC
                elif "com.mumfrey" in lib_name or "org.spongepowered" in lib_name:
                    if self.state == INSTANCE_STATE_ORIGINAL:
                        self.state = INSTANCE_STATE_LITELOADER
        
        # 设置版本号
        id_str = self.json_object.get("id", self.name)
        self.version = MinecraftVersion(id_str)
        
        # 从 libraries 提取详细版本信息
        for lib in libraries:
            lib_name = lib.get("name", "")
            parts = lib_name.split(":")
            if len(parts) >= 3:
                group, artifact, version_str = parts[0], parts[1], parts[2]
                
                if "forge" in artifact.lower() and "forge" not in group.lower():
                    self.version.forge = version_str
                elif "fabric" in artifact.lower():
                    self.version.fabric = version_str
                elif "neoforge" in artifact.lower() or group == "net.neoforged":
                    self.version.neoforge = version_str
    
    def get_main_class(self) -> str:
        # 获取主类名
        if self.json_object:
            return self.json_object.get("mainClass", "net.minecraft.client.main.Main")
        return "net.minecraft.client.main.Main"
    
    def get_libraries(self) -> list:
        # 获取库文件列表
        if self.json_object:
            return self.json_object.get("libraries", [])
        return []
    
    def get_arguments(self) -> list:
        # 获取 JVM 参数
        args = []
        if self.json_object:
            arguments = self.json_object.get("arguments", {})
            if isinstance(arguments, dict):
                args = arguments.get("jvm", [])
        return args
    
    def get_game_arguments(self) -> str:
        # 获取游戏参数
        if self.json_object:
            arguments = self.json_object.get("arguments", {})
            if isinstance(arguments, dict):
                game_args = arguments.get("game", [])
                # 将列表参数合并为字符串
                result = []
                for arg in game_args:
                    if isinstance(arg, str):
                        result.append(arg)
                    elif isinstance(arg, dict):
                        # rules 规则暂不处理
                        pass
                return " ".join(result)
            
            # 旧格式
            return self.json_object.get("minecraftArguments", "")
        return ""
    
    def get_asset_index(self) -> str:
        # 获取资源索引 ID
        if self.json_object:
            asset_index = self.json_object.get("assetIndex", {})
            if isinstance(asset_index, dict):
                return asset_index.get("id", "")
        return ""
    
    def get_java_version(self) -> int:
        # 获取需要的 Java 版本
        if self.json_object:
            java_version = self.json_object.get("javaVersion", {})
            if isinstance(java_version, dict):
                return java_version.get("majorVersion", 8)
        return 8
    
    def is_modable(self) -> bool:
        # 是否可以安装 Mod
        return self.state in (INSTANCE_STATE_FORGE, INSTANCE_STATE_NEOFORGE,
                              INSTANCE_STATE_FABRIC, INSTANCE_STATE_LITELOADER,
                              INSTANCE_STATE_ORIGINAL, INSTANCE_STATE_SNAPSHOT)
    
    def get_state_name(self) -> str:
        # 获取状态中文名
        names = {
            INSTANCE_STATE_ERROR: "错误",
            INSTANCE_STATE_ORIGINAL: "Vanilla",
            INSTANCE_STATE_SNAPSHOT: "Per-Vanilla",
            INSTANCE_STATE_FOOL: "Fool",
            INSTANCE_STATE_OPTIFINE: "OptiFine",
            INSTANCE_STATE_OLD: "Vanilla",
            INSTANCE_STATE_FORGE: "Forge",
            INSTANCE_STATE_NEOFORGE: "NeoForge",
            INSTANCE_STATE_LITELOADER: "LiteLoader",
            INSTANCE_STATE_FABRIC: "Fabric",
        }
        return names.get(self.state, "未知")
    
    def __repr__(self):
        return f"McInstance({self.name}, {self.get_state_name()})"


class MinecraftVersion:
    # Minecraft 版本号
    
    def __init__(self, name: str = ""):
        self.vanilla_name = name
        self.vanilla = self._parse_version(name)
        self.forge = ""
        self.neoforge = ""
        self.fabric = ""
        self.has_liteloader = False
        self.reliable = False
    
    def _parse_version(self, name: str) -> list:
        # 解析版本号为可比较的列表
        import re
        parts = re.findall(r'\d+', name)
        return [int(p) for p in parts[:3]] if parts else [0]
    
    @property
    def vaild(self) -> bool:
        # 版本号是否有效
        return len(self.vanilla) > 0 and self.vanilla[0] < 1000
    
    def __repr__(self):
        parts = [self.vanilla_name]
        if self.forge:
            parts.append(f"Forge {self.forge}")
        if self.neoforge:
            parts.append(f"NeoForge {self.neoforge}")
        if self.fabric:
            parts.append(f"Fabric {self.fabric}")
        if self.has_liteloader:
            parts.append("LiteLoader")
        return ", ".join(parts)


def get_available_minecraft_versions(manifest: dict | None = None) -> list[dict]:
    # 获取可用的 Minecraft 版本列表
    if manifest is None:
        manifest = net_get_manifest()
    
    if not manifest:
        return []
    
    return manifest.get("versions", [])


def get_version_json(version_id: str, use_mirror: bool = True) -> dict | None:
    # 获取特定版本的 version.json
    manifest = net_get_manifest(use_mirror)
    if not manifest:
        return None
    
    # 找到版本的 URL
    version_url = None
    for v in manifest.get("versions", []):
        if v.get("id") == version_id:
            version_url = v.get("url", "")
            break
    
    if not version_url:
        log_error(f"未找到版本 {version_id}")
        return None
    
    # 下载 version.json
    code, data = net_request(version_url, use_mirror=use_mirror)
    if code == 200 and isinstance(data, dict):
        return data
    
    return None


def install_minecraft_version(version_id: str, folder: MinecraftFolder,
                               use_mirror: bool = True) -> bool:
    # 安装 Minecraft 版本
    #
    # 1. 获取 version.json
    # 2. 下载 client.jar
    # 3. 下载 libraries
    # 4. 下载 assets
    log_info(f"开始安装 Minecraft {version_id}...")
    
    # 确保版本目录存在
    version_dir = os.path.join(folder.versions_dir, version_id)
    os.makedirs(version_dir, exist_ok=True)
    
    json_path = os.path.join(version_dir, f"{version_id}.json")
    jar_path = os.path.join(version_dir, f"{version_id}.jar")
    
    # 获取 version manifest
    manifest = net_get_manifest(use_mirror)
    if not manifest:
        log_error("获取版本清单失败")
        return False
    
    # 查找版本 URL
    version_url = None
    for v in manifest.get("versions", []):
        if v.get("id") == version_id:
            version_url = v.get("url", "")
            break
    
    if not version_url:
        log_error(f"未找到版本 {version_id}")
        return False
    
    # 下载 version.json
    log_info("正在下载 version.json...")
    code, json_data = net_request(version_url, use_mirror=use_mirror)
    if code != 200 or not isinstance(json_data, dict):
        log_error("下载 version.json 失败")
        return False
    
    # 保存 version.json
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(json_data, f, indent=2)
        log_success("version.json 下载完成")
    except Exception as e:
        log_error(f"保存 version.json 失败: {e}")
        return False
    
    # 下载 client.jar
    downloads = json_data.get("downloads", {})
    client_info = downloads.get("client", {})
    client_url = client_info.get("url", "")
    
    if client_url:
        if use_mirror:
            client_url = client_url.replace("https://launcher.mojang.com", BMCLAPI_MIRROR)
            client_url = client_url.replace("https://resources.download.minecraft.net", f"{BMCLAPI_MIRROR}/assets")
        ok, fail = download_multi([DownloadTask(url=client_url, save_path=jar_path, name="client.jar")], desc="client.jar")
        if fail > 0:
            log_error("client.jar 下载失败")
            return False
    else:
        log_warn("未找到 client.jar 下载地址")
        return False

    # 下载 libraries（多线程）
    log_info("正在下载 libraries...")
    libraries = json_data.get("libraries", [])
    lib_tasks: list[DownloadTask] = []
    
    for lib in libraries:
        lib_name = lib.get("name", "未知")
        downloads_info = lib.get("downloads", {})
        artifact = downloads_info.get("artifact", {})
        lib_url = artifact.get("url", "")
        lib_path = artifact.get("path", "")
        
        # 处理操作系统规则
        rules = lib.get("rules", [])
        if not _check_rules(rules):
            log_debug(f"跳过(规则不匹配): {lib_name}")
            continue
        
        # 处理 natives
        classifiers = downloads_info.get("classifiers", {})
        if classifiers:
            os_name = detect_os()
            for key in classifiers:
                if os_name in key:
                    native_info = classifiers[key]
                    native_url = native_info.get("url", "")
                    native_path = native_info.get("path", "")
                    if native_url and native_path:
                        dest = os.path.join(folder.libraries_dir, native_path)
                        if not os.path.exists(dest):
                            lib_tasks.append(DownloadTask(
                                url=native_url, save_path=dest,
                                name=os.path.basename(dest)))
                    break
            continue
        
        if lib_url and lib_path:
            dest = os.path.join(folder.libraries_dir, lib_path)
            if not os.path.exists(dest):
                lib_tasks.append(DownloadTask(
                    url=lib_url, save_path=dest,
                    name=os.path.basename(dest)))
    
    if lib_tasks:
        ok, fail = download_multi(lib_tasks, desc="Libraries")
        if fail > 0:
            log_error("Libraries 下载失败")
            return False

    # 下载 assets
    log_info("正在下载 assets...")
    asset_index = json_data.get("assetIndex", {})
    asset_url = asset_index.get("url", "")
    asset_id = asset_index.get("id", "")
    
    if asset_url and asset_id:
        if use_mirror:
            asset_url = asset_url.replace("https://launchermeta.mojang.com", BMCLAPI_MIRROR)
        
        asset_dir = os.path.join(folder.assets_dir, "indexes")
        os.makedirs(asset_dir, exist_ok=True)
        asset_index_path = os.path.join(asset_dir, f"{asset_id}.json")
        
        if net_download(asset_url, asset_index_path):
            log_success("资源索引下载完成")
            
            # 下载资源文件（多线程）
            try:
                with open(asset_index_path, "r", encoding="utf-8") as f:
                    index_data = json.load(f)
                
                objects = index_data.get("objects", {})
                asset_tasks: list[DownloadTask] = []
                for obj_key, obj_info in objects.items():
                    obj_hash = obj_info.get("hash", "")
                    if not obj_hash:
                        continue
                    obj_subdir = obj_hash[:2]
                    obj_src_path = os.path.join(folder.assets_dir, "objects", obj_subdir, obj_hash)
                    if not os.path.exists(obj_src_path):
                        dl_url = f"{BMCLAPI_MIRROR}/assets/{obj_hash[:2]}/{obj_hash}"
                        os.makedirs(os.path.dirname(obj_src_path), exist_ok=True)
                        asset_tasks.append(DownloadTask(
                            url=dl_url, save_path=obj_src_path,
                            name=obj_hash[:12]))
                
                if asset_tasks:
                    log_info(f"需要下载 {len(asset_tasks)} 个资源文件")
                    ok, fail = download_multi(asset_tasks, desc="Assets")
                    if fail > 0:
                        log_error("Assets 下载失败")
                        return False
                else:
                    log_info("  所有资源文件已存在，无需下载")

                log_success("资源文件处理完成")
                log_success(f"Minecraft {version_id} 安装完成")
                return True
            except Exception as e:
                log_warn(f"处理资源文件时出错: {e}")
                return False
        else:
            log_error("资源索引下载失败")
            return False

    log_error("缺少资源索引信息")
    return False
