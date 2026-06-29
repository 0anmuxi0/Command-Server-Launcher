# 整合包管理模块
#
# 支持的整合包格式:
#   - CurseForge (manifest.json)
#   - Modrinth (modrinth.index.json)
#   - HMCL (modpack.json)
#   - MultiMC (mmc-pack.json)
#   - MCBBS (mcbbs.packmeta)

import os
import json
import zipfile
import tempfile
import shutil
from .logger import log_info, log_debug, log_warn, log_error, log_success, log_request
from .network import net_request, net_download
from .minecraft import MinecraftFolder, install_minecraft_version
from .downloader import download_multi, DownloadTask


def detect_modpack_type(zip_path: str) -> tuple[int, str, str]:
    # 检测整合包类型
    #
    # 返回: (类型编号, 类型名称, 描述)
    #
    # 类型:
    #   0 - CurseForge
    #   1 - HMCL
    #   2 - MultiMC
    #   3 - MCBBS
    #   4 - Modrinth
    #   9 - 带启动器的压缩包
    #   -1 - 未识别
    if not os.path.exists(zip_path):
        log_error(f"文件不存在: {zip_path}")
        return (-1, "", "文件不存在")
    
    log_info(f"正在检测整合包类型: {zip_path}")
    
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 获取所有文件列表（包括可能顶层的文件夹）
            all_files = zf.namelist()
            all_names = [os.path.basename(f) for f in all_files]
            top_files = set()
            for f in all_files:
                parts = f.replace("\\", "/").split("/")
                if len(parts) >= 1:
                    top_files.add(parts[0])
                    if len(parts) >= 2:
                        top_files.add("/".join(parts[:2]))
            
            log_debug(f"压缩包内顶层文件和文件夹: {top_files}")
            
            # 检测 Modrinth
            if "modrinth.index.json" in all_names or "modrinth.index.json" in top_files:
                log_success("检测到 Modrinth 整合包")
                return (4, "Modrinth", "Modrinth 整合包")
            
            # 检测 CurseForge
            if "manifest.json" in all_names:
                # 读取 manifest.json 判断是否有 addons
                try:
                    manifest_data = json.loads(zf.read("manifest.json"))
                    if "manifestType" in manifest_data and manifest_data.get("manifestType") == "minecraftModpack":
                        log_info("检测到 CurseForge 整合包")
                        return (0, "CurseForge", "CurseForge 整合包")
                    if "addons" not in manifest_data:
                        log_info("检测到 CurseForge 整合包")
                        return (0, "CurseForge", "CurseForge 整合包")
                    else:
                        # 有 addons 的是 MCBBS
                        log_info("检测到 MCBBS 整合包")
                        return (3, "MCBBS", "MCBBS 整合包")
                except:
                    pass
            
            # 检测 MCBBS
            if "mcbbs.packmeta" in all_names or "mcbbs.packmeta" in top_files:
                log_info("检测到 MCBBS 整合包")
                return (3, "MCBBS", "MCBBS 整合包")
            
            # 检测 HMCL
            if "modpack.json" in all_names or "modpack.json" in top_files:
                log_info("检测到 HMCL 整合包")
                return (1, "HMCL", "HMCL 整合包")
            
            # 检测 MultiMC
            if "mmc-pack.json" in all_names or "mmc-pack.json" in top_files:
                log_info("检测到 MultiMC 整合包")
                return (2, "MultiMC", "MultiMC 整合包")
            
            # 检测带启动器的压缩包
            if "modpack.zip" in all_names or "modpack.mrpack" in all_names:
                log_info("检测到带启动器的压缩包")
                return (9, "LauncherPack", "带启动器的压缩包")
            
            # 未识别
            log_warn("未识别的整合包格式，作为普通压缩包处理")
            return (-1, "Unknown", "无法识别的整合包格式")
    
    except zipfile.BadZipFile:
        log_error("文件不是有效的 ZIP 压缩包")
        return (-1, "", "文件损坏")
    except Exception as e:
        log_error(f"检测整合包时出错: {e}")
        return (-1, "", str(e))


def install_modpack(zip_path: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装整合包
    #
    # 参数:
    #     zip_path: 整合包文件路径
    #     folder: Minecraft 文件夹
    #     instance_name: 版本实例名（可选，自动生成）
    #
    # 返回: 是否安装成功
    pack_type, type_name, _ = detect_modpack_type(zip_path)
    
    if pack_type < 0:
        log_error("不支持的整合包格式")
        return False
    
    log_info(f"开始安装 {type_name} 整合包...")
    
    # 使用临时目录解压
    temp_dir = tempfile.mkdtemp(prefix="cml_modpack_")
    
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(temp_dir)
        
        if pack_type == 0:
            return _install_curseforge(temp_dir, folder, instance_name)
        elif pack_type == 1:
            return _install_hmcl(temp_dir, folder, instance_name)
        elif pack_type == 2:
            return _install_multimc(temp_dir, folder, instance_name)
        elif pack_type == 3:
            return _install_mcbbs(temp_dir, folder, instance_name)
        elif pack_type == 4:
            return _install_modrinth(temp_dir, folder, instance_name)
        elif pack_type == 9:
            return _install_launcher_pack(temp_dir, folder, instance_name)
        else:
            log_error(f"不支持的整合包类型: {type_name}")
            return False
    
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(temp_dir, ignore_errors=True)
        except:
            pass


def _install_curseforge(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装 CurseForge 整合包
    log_info("安装 CurseForge 整合包...")
    
    manifest_path = os.path.join(temp_dir, "manifest.json")
    if not os.path.exists(manifest_path):
        log_error("未找到 manifest.json")
        return False
    
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        log_error(f"读取 manifest.json 失败: {e}")
        return False
    
    # 获取版本信息
    mc_version = manifest.get("minecraft", {}).get("version", "")
    mod_loaders = manifest.get("minecraft", {}).get("modLoaders", [])
    loader_type = ""
    loader_version = ""
    
    for loader in mod_loaders:
        loader_id = loader.get("id", "")
        if "forge" in loader_id.lower():
            loader_type = "forge"
            loader_version = loader_id.split("-")[-1] if "-" in loader_id else ""
        elif "fabric" in loader_id.lower():
            loader_type = "fabric"
            loader_version = loader_id.split("-")[-1] if "-" in loader_id else ""
        elif "neoforge" in loader_id.lower():
            loader_type = "neoforge"
            loader_version = loader_id.split("-")[-1] if "-" in loader_id else ""
    
    if not mc_version:
        log_error("无法获取 Minecraft 版本")
        return False
    
    # 确定实例名
    if not instance_name:
        pack_name = manifest.get("name", f"CurseForge-{mc_version}")
        instance_name = _sanitize_name(pack_name)
    
    log_info(f"Minecraft: {mc_version}")
    log_info(f"模组加载器: {loader_type} {loader_version}")
    log_info(f"实例名: {instance_name}")
    
    # 安装 Minecraft 版本
    if not install_minecraft_version(mc_version, folder):
        log_error("安装 Minecraft 版本失败")
        return False
    
    # 解压 overrides
    overrides_dir = os.path.join(temp_dir, "overrides")
    if os.path.exists(overrides_dir):
        _copy_overrides(overrides_dir, folder, instance_name)
    
    _ensure_instance_json(instance_name, mc_version, folder)
    log_success(f"CurseForge 整合包安装完成: {instance_name}")
    return True


def _install_modrinth(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装 Modrinth 整合包
    log_info("安装 Modrinth 整合包...")
    
    index_path = os.path.join(temp_dir, "modrinth.index.json")
    if not os.path.exists(index_path):
        log_error("未找到 modrinth.index.json")
        return False
    
    try:
        with open(index_path, "r", encoding="utf-8") as f:
            index = json.load(f)
    except Exception as e:
        log_error(f"读取 modrinth.index.json 失败: {e}")
        return False
    
    # 获取版本信息
    deps = index.get("dependencies", {})
    mc_version = deps.get("minecraft", "")
    loader_type = ""
    loader_version = ""
    
    for loader_name in ["forge", "fabric", "quilt", "neoforge"]:
        val = deps.get(loader_name, "")
        if val:
            loader_type = loader_name
            loader_version = val
            break
    
    if not mc_version:
        log_error("无法获取 Minecraft 版本")
        return False
    
    # 确定实例名
    if not instance_name:
        pack_name = index.get("name", f"Modrinth-{mc_version}")
        instance_name = _sanitize_name(pack_name)
    
    log_info(f"Minecraft: {mc_version}")
    log_info(f"Mod Loader: {loader_type} {loader_version}")
    log_info(f"实例名: {instance_name}")
    
    # 安装 Minecraft 版本
    if not install_minecraft_version(mc_version, folder):
        log_error("安装 Minecraft 版本失败")
        return False
    
    # 解压 overrides
    for override_dir in ["overrides", "client-overrides"]:
        override_path = os.path.join(temp_dir, override_dir)
        if os.path.exists(override_path):
            _copy_overrides(override_path, folder, instance_name)
    
    # 下载 Modrinth 文件（多线程）
    files = index.get("files", [])
    if files:
        log_info(f"正在下载 {len(files)} 个 Modrinth 文件...")
        modrinth_tasks: list[DownloadTask] = []
        for file_info in files:
            file_path = file_info.get("path", "")
            downloads = file_info.get("downloads", [])
            if downloads and file_path:
                file_url = downloads[0]
                dest = os.path.join(folder.versions_dir, instance_name, file_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                modrinth_tasks.append(DownloadTask(
                    url=file_url, save_path=dest,
                    name=os.path.basename(file_path)))
        if modrinth_tasks:
            ok, fail = download_multi(modrinth_tasks, desc="Modrinth")
            if fail > 0:
                log_error("Modrinth 文件下载失败")
                return False

    # 下载 Mod Loader（如 NeoForge/Forge/Fabric）
    if not _download_mod_loader(loader_type, loader_version, mc_version, folder, instance_name):
        return False
    _ensure_instance_json(instance_name, mc_version, folder)
    log_success(f"Modrinth 整合包安装完成: {instance_name}")
    return True


def _install_hmcl(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装 HMCL 整合包
    log_info("安装 HMCL 整合包...")
    
    pack_path = os.path.join(temp_dir, "modpack.json")
    if not os.path.exists(pack_path):
        log_error("未找到 modpack.json")
        return False
    
    try:
        with open(pack_path, "r", encoding="utf-8") as f:
            pack = json.load(f)
    except Exception as e:
        log_error(f"读取 modpack.json 失败: {e}")
        return False
    
    mc_version = pack.get("gameVersion", "")
    pack_name = pack.get("name", f"HMCL-{mc_version}")
    instance_name = instance_name or _sanitize_name(pack_name)
    
    if mc_version:
        if not install_minecraft_version(mc_version, folder):
            log_error("安装 Minecraft 版本失败")
            return False
    # 解压 minecraft 目录
    mc_dir = os.path.join(temp_dir, "minecraft")
    if os.path.exists(mc_dir):
        _copy_overrides(mc_dir, folder, instance_name)
    
    _ensure_instance_json(instance_name, mc_version, folder)
    log_success(f"HMCL 整合包安装完成: {instance_name}")
    return True


def _install_multimc(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装 MultiMC 整合包
    log_info("安装 MultiMC 整合包...")
    
    pack_path = os.path.join(temp_dir, "mmc-pack.json")
    if not os.path.exists(pack_path):
        log_error("未找到 mmc-pack.json")
        return False
    
    try:
        with open(pack_path, "r", encoding="utf-8") as f:
            pack = json.load(f)
    except Exception as e:
        log_error(f"读取 mmc-pack.json 失败: {e}")
        return False
    
    # 从 components 获取版本
    mc_version = ""
    for component in pack.get("components", []):
        if component.get("uid") == "net.minecraft":
            mc_version = component.get("version", "")
            break
    
    if not instance_name:
        # 尝试从 instance.cfg 读取名称
        cfg_path = os.path.join(temp_dir, "instance.cfg")
        if os.path.exists(cfg_path):
            name = _read_multimc_cfg(cfg_path, "name")
            instance_name = _sanitize_name(name or f"MMC-{mc_version}")
        else:
            instance_name = f"MMC-{mc_version}" if mc_version else "MultiMC-Pack"
    
    if mc_version:
        if not install_minecraft_version(mc_version, folder):
            log_error("安装 Minecraft 版本失败")
            return False

    # 复制文件
    for item in [".minecraft", "minecraft"]:
        src = os.path.join(temp_dir, item)
        if os.path.exists(src):
            _copy_overrides(src, folder, instance_name)
            break
    
    _ensure_instance_json(instance_name, mc_version, folder)
    log_success(f"MultiMC 整合包安装完成: {instance_name}")
    return True


def _install_mcbbs(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装 MCBBS 整合包
    log_info("安装 MCBBS 整合包...")
    
    manifest_path = os.path.join(temp_dir, "manifest.json")
    packmeta_path = os.path.join(temp_dir, "mcbbs.packmeta")
    
    mc_version = ""
    pack_name = ""
    
    if os.path.exists(packmeta_path):
        try:
            with open(packmeta_path, "r", encoding="utf-8") as f:
                packmeta = json.load(f)
            mc_version = packmeta.get("addons", {}).get("game", "")
            pack_name = packmeta.get("name", "")
        except:
            pass
    
    if not mc_version and os.path.exists(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            addons = manifest.get("addons", [])
            for addon in addons:
                if addon.get("id") == "game":
                    mc_version = addon.get("version", "")
            pack_name = manifest.get("name", pack_name)
        except:
            pass
    
    instance_name = instance_name or _sanitize_name(pack_name or f"MCBBS-{mc_version or 'unknown'}")
    
    if mc_version:
        if not install_minecraft_version(mc_version, folder):
            log_error("安装 Minecraft 版本失败")
            return False

    # 解压 overrides
    for override_dir in ["overrides", "minecraft"]:
        override_path = os.path.join(temp_dir, override_dir)
        if os.path.exists(override_path):
            _copy_overrides(override_path, folder, instance_name)
            break
    
    _ensure_instance_json(instance_name, mc_version, folder)
    log_success(f"MCBBS 整合包安装完成: {instance_name}")
    return True


def _install_launcher_pack(temp_dir: str, folder: MinecraftFolder, instance_name: str | None = None) -> bool:
    # 安装带启动器的压缩包
    log_info("安装启动器整合包...")
    
    # 查找 modpack.zip 或 modpack.mrpack
    pack_file = None
    for f in os.listdir(temp_dir):
        if f in ("modpack.zip", "modpack.mrpack"):
            pack_file = os.path.join(temp_dir, f)
            break
    
    if pack_file:
        # 递归解压
        pack_extract_dir = os.path.join(temp_dir, "_pack_extracted")
        with zipfile.ZipFile(pack_file, "r") as zf:
            zf.extractall(pack_extract_dir)
        
        # 尝试检测内部整合包类型
        return install_modpack(pack_file, folder, instance_name)
    
    log_warn("未找到 modpack.zip 或 modpack.mrpack，尝试直接复制...")
    
    # 直接复制 .minecraft 目录
    mc_src = os.path.join(temp_dir, ".minecraft")
    if os.path.exists(mc_src):
        _copy_overrides(mc_src, folder, instance_name or "LauncherPack")
        log_success("启动器整合包安装完成")
        return True
    
    log_error("无法识别启动器整合包结构")
    return False


def _copy_overrides(src_dir: str, folder: MinecraftFolder, instance_name: str):
    # 复制 overrides 目录到版本文件夹
    version_dir = os.path.join(folder.versions_dir, instance_name)
    os.makedirs(version_dir, exist_ok=True)
    
    log_info(f"正在复制文件到 {version_dir}...")
    
    for item in os.listdir(src_dir):
        src_item = os.path.join(src_dir, item)
        dst_item = os.path.join(version_dir, item)
        
        try:
            if os.path.isdir(src_item):
                if os.path.exists(dst_item):
                    shutil.rmtree(dst_item, ignore_errors=True)
                shutil.copytree(src_item, dst_item)
            else:
                shutil.copy2(src_item, dst_item)
        except Exception as e:
            log_warn(f"复制 {item} 失败: {e}")


def _download_mod_loader(loader_type: str, loader_version: str, mc_version: str,
                         folder: MinecraftFolder, instance_name: str) -> bool:
    # 下载 Mod Loader JAR 到实例的 mods 目录
    if not loader_type or not loader_version:
        return True
    log_info(f"正在下载 {loader_type} {loader_version}...")

    urls: list[tuple[str, str]] = []  # (url, filename)
    if loader_type == "neoforge":
        candidates = [
            f"neoforge-{loader_version}-universal.jar",
            f"neoforge-{loader_version}-installer.jar",
            f"neoforge-{loader_version}-userdev.jar",
        ]
        base_hosts = [
            "https://maven.neoforged.net/releases/net/neoforged/neoforge",
            "https://bmclapi2.bangbang93.com/maven/net/neoforged/neoforge",
        ]
        for host in base_hosts:
            for name in candidates:
                urls.append((f"{host}/{loader_version}/{name}", name))
    elif loader_type == "forge":
        filename = f"forge-{mc_version}-{loader_version}.jar"
        urls = [
            (f"https://maven.minecraftforge.net/net/minecraftforge/forge/{mc_version}-{loader_version}/{filename}", filename),
            (f"https://bmclapi2.bangbang93.com/maven/net/minecraftforge/forge/{mc_version}-{loader_version}/{filename}", filename),
        ]
    elif loader_type == "fabric":
        filename = f"fabric-loader-{loader_version}.jar"
        urls = [
            (f"https://maven.fabricmc.net/net/fabricmc/fabric-loader/{loader_version}/{filename}", filename),
            (f"https://bmclapi2.bangbang93.com/maven/net/fabricmc/fabric-loader/{loader_version}/{filename}", filename),
        ]
    elif loader_type == "quilt":
        filename = f"quilt-loader-{loader_version}.jar"
        urls = [
            (f"https://maven.quiltmc.org/repository/release/org/quiltmc/quilt-loader/{loader_version}/{filename}", filename),
        ]
    else:
        log_warn(f"不支持的加载器: {loader_type}")
        return False

    dest_dir = os.path.join(folder.versions_dir, instance_name, "mods")
    os.makedirs(dest_dir, exist_ok=True)

    # 尝试所有 URL 和候选文件名，直到成功
    for url, filename in urls:
        dest = os.path.join(dest_dir, filename)
        if os.path.exists(dest):
            log_info(f"  {filename} 已存在，跳过")
            return True

        task = DownloadTask(url=url, save_path=dest, name=filename)
        ok, fail = download_multi([task], desc=loader_type, timeout=60)
        if fail == 0:
            return True
        log_warn(f"  {url} 失败，尝试下一个...")

    log_warn(f"  {loader_type} {loader_version} 下载失败，请手动安装")
    return False


def _ensure_instance_json(instance_name: str, mc_version: str, folder: MinecraftFolder):
    # 确保实例目录存在 .json 文件，否则创建继承自原版的 JSON
    json_path = os.path.join(folder.versions_dir, instance_name, f"{instance_name}.json")
    if os.path.exists(json_path):
        return
    # 创建最小 JSON，继承自原版版本
    import datetime
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S+00:00")
    inst_json = {
        "id": instance_name,
        "inheritsFrom": mc_version,
        "time": now,
        "releaseTime": now,
        "type": "release",
        "mainClass": "net.minecraft.client.main.Main",
        "libraries": [],
    }
    try:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(inst_json, f, indent=2)
        log_debug(f"已创建实例 JSON: {json_path}")
    except Exception as e:
        log_warn(f"创建实例 JSON 失败: {e}")


def _sanitize_name(name: str) -> str:
    # 清理名称中的非法字符
    import re
    # 移除 Windows 文件名非法字符
    name = re.sub(r'[<>:"/\\|?*]', "", name)
    # 限制长度
    if len(name) > 100:
        name = name[:100]
    return name.strip()


def _read_multimc_cfg(cfg_path: str, key: str) -> str:
    # 读取 MultiMC instance.cfg 中的配置项
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith(f"{key}="):
                    return line.strip().split("=", 1)[1]
    except:
        pass
    return ""
