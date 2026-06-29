# CMD-Minecraft-Launcher - 主入口
# 纯命令行 Minecraft 启动器
# 基于 Plain Craft Launcher 2 设计
#
# 功能:
#   - 账号登录 (离线/微软/Yggdrasil)
#   - 整合包安装 (CurseForge/Modrinth/HMCL/MultiMC/MCBBS)
#   - Minecraft 版本管理
#   - 游戏启动

import os
import sys
import time
import subprocess

# 确保能导入 launcher 包
script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from launcher.logger import (INFO, DEBUG, ERROR, WARN, REQUEST, SUCCESS, RESET,
                              log_info, log_debug, log_error, log_warn,
                              log_success, log_request, log_input)
from launcher.config import ConfigManager, AccountManager, DOT_MINECRAFT
from launcher.login import (login_offline, login_microsoft, login_yggdrasil,
                             LOGIN_TYPE_LEGACY, LOGIN_TYPE_MS, LOGIN_TYPE_AUTH)
from launcher.minecraft import (MinecraftFolder, MinecraftInstance,
                                 get_available_minecraft_versions,
                                 install_minecraft_version, net_get_manifest,
                                 INSTANCE_STATE_ORIGINAL)
from launcher.modpack import detect_modpack_type, install_modpack
from launcher.launch import LaunchOptions, find_java, launch_minecraft
from launcher.downloader import init as init_downloader


def main():
    os.system("title Command Prompt Minecraft Launcher")
    # 设置控制台为 UTF-8 编码，防止中文乱码
    if os.name == "nt":
        os.system("chcp 65001 >nul")
    # 主函数
    log_success("Command Prompt Minecraft Launcher - 纯命令行 MC 启动器")
    
    # 初始化配置
    config = ConfigManager()
    account_mgr = AccountManager()
    
    # 初始化下载器（传入 ConfigManager 统一管理配置）
    init_downloader(config)
    
    # 初始化 Minecraft 文件夹
    game_dir = config.get("Launch", "GameDirectory", DOT_MINECRAFT)
    mc_folder = MinecraftFolder("默认", game_dir)
    mc_folder.ensure_dirs()
    
    while True:
        try:
            show_menu(config, account_mgr, mc_folder)
            choice = log_input("请选择操作: ").strip()
            
            if choice == "0":
                log_info("感谢使用 CMD-Minecraft-Launcher，再见!")
                # 等待3秒
                time.sleep(3)
                break
            elif choice == "1":
                handle_login(account_mgr)
            elif choice == "2":
                handle_launch(config, account_mgr, mc_folder)
            elif choice == "3":
                handle_install_version(config, mc_folder)
            elif choice == "4":
                handle_install_modpack(config, mc_folder)
            elif choice == "5":
                handle_manage_versions(mc_folder)
            elif choice == "6":
                handle_settings(config)
            else:
                log_warn("无效的选项，请重新输入")
            
        except KeyboardInterrupt:
            log_info("用户取消操作")
            break
        except Exception as e:
            log_error(f"发生错误: {e}")
            log_debug(f"详细信息: {e}")
    


def show_menu(config: ConfigManager, account_mgr: AccountManager, mc_folder: MinecraftFolder):
    # 显示主菜单
    active_account = account_mgr.get_active_account()
    account_name = active_account.get("name", "未登录") if active_account else "未登录"
    account_type = {
        "legacy": "离线",
        "microsoft": "微软",
        "auth": "第三方",
    }.get(active_account.get("type", "") if active_account else "", "未登录")
    
    java_path = config.get("Launch", "JavaPath", "自动检测")
    if not java_path or java_path == "":
        java_path = "自动检测"
    
    log_info("[1] 账号管理")
    log_info("[2] 启动游戏")
    log_info("[3] 安装 Minecraft 版本")
    log_info("[4] 安装整合包")
    log_info("[5] 版本管理")
    log_info("[6] 设置")
    log_info("[0] 退出")


def handle_login(account_mgr: AccountManager):
    # 处理登录
    while True:
        accounts = account_mgr.list_accounts()
        
        # 先展示已保存的账号（用编号而非 [n]，避免与菜单冲突）
        if accounts:
            log_info(" 已保存的账号:")
            for i, acc in enumerate(accounts):
                active = "=>" if acc.get("active") else "  "
                type_name = {
                    "legacy": "离线",
                    "microsoft": "微软",
                    "auth": "第三方",
                }.get(acc.get("type", ""), acc.get("type", "未知"))
                log_info(f"{active} {acc.get('name', '未知')} ({type_name},{i + 1})")
        else:
            log_info("暂无已保存的账号")

        log_info("[1] 选择/切换账号")
        log_info("[2] 添加账号")
        log_info("[d] 删除账号")
        log_info("[0] 返回主菜单")

        
        choice = log_input("请选择: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            _do_select_account(account_mgr)
        elif choice == "2":
            log_info("[1] 离线登录")
            log_info("[2] 正版登录")
            log_info("[3] 第三方登录")
            log_info("[0] 返回")

            choice = log_input("请选择: ").strip()
            if choice == "0":
                break
            elif choice == "1":
                _do_offline_login(account_mgr)
            elif choice == "2":
                _do_microsoft_login(account_mgr)
            elif choice == "3":
                _do_yggdrasil_login(account_mgr)
            
        elif choice.startswith("d"):
            _do_delete_account(account_mgr, choice)
        else:
            log_warn("无效的选项")
        



def _do_offline_login(account_mgr: AccountManager):
    # 离线登录
    username = log_input("请输入用户名: ").strip()
    if not username:
        log_error("用户名不能为空")
        return
    
    result = login_offline(username)
    if result:
        result["active"] = True
        account_mgr.add_account(result)
        log_success("账号已保存")


def _do_microsoft_login(account_mgr: AccountManager):
    # 微软登录
    log_info("开始微软登录流程...")
    result = login_microsoft()
    if result:
        result["active"] = True
        account_mgr.add_account(result)
        log_success("微软账号已保存")


def _do_yggdrasil_login(account_mgr: AccountManager):
    # 第三方 Yggdrasil 登录
    server_url = log_input("请输入认证服务器地址: ").strip()
    if not server_url:
        log_error("服务器地址不能为空")
        return
    
    username = log_input("请输入用户名/邮箱: ").strip()
    if not username:
        log_error("用户名不能为空")
        return
    
    password = log_input("请输入密码: ").strip()
    if not password:
        log_error("密码不能为空")
        return
    
    result = login_yggdrasil(server_url, username, password)
    if result:
        result["active"] = True
        account_mgr.add_account(result)
        log_success("第三方账号已保存")


def _do_select_account(account_mgr: AccountManager):
    # 选择账号
    accounts = account_mgr.list_accounts()
    if not accounts:
        log_warn("没有可用的账号")
        return
    
    idx_str = log_input("请输入要选择的账号序号: ").strip()
    try:
        idx = int(idx_str) - 1  # 转换为 0-based 索引
        if 0 <= idx < len(accounts):
            account_mgr.set_active(idx)
            acc = accounts[idx]
            log_success(f"已选择账号: {acc.get('name', '未知')}")
        else:
            log_error("无效的序号")
    except (ValueError, IndexError):
        log_error("无效的序号")


def _do_delete_account(account_mgr: AccountManager, choice: str = ""):
    # 删除账号（支持多选，如 d012 删除第 0/1/2 号）
    accounts = account_mgr.list_accounts()
    if not accounts:
        log_warn("没有可删除的账号")
        return
    
    idx_str = choice[1:].strip() if choice.startswith("d") else ""
    if not idx_str:
        idx_str = log_input("请输入要删除的账号序号 (如 012): ").strip()
    
    names = []
    for ch in idx_str:
        try:
            idx = int(ch)
            if 0 <= idx < len(accounts):
                acc = accounts[idx]
                if account_mgr.remove_account(idx):
                    names.append(acc.get("name", "未知"))
                    log_success(f"已删除账号: {acc.get('name', '未知')}")
            else:
                log_warn(f"无效的序号: {idx}")
        except ValueError:
            pass
    if names:
        log_info(f"共删除 {len(names)} 个账号")


def handle_launch(config: ConfigManager, account_mgr: AccountManager, mc_folder: MinecraftFolder):
    # 处理启动游戏
    # 扫描版本
    versions = mc_folder.scan_versions()
    if not versions:
        log_warn("未找到任何 Minecraft 版本")
        log_info("请先使用 [3] 安装 Minecraft 版本 或 [4] 安装整合包")
        return
    # 版本列表序号从1开始
    for i, ver in enumerate(versions):
        state_name = ver.get_state_name()
        log_info(f"[{i + 1}] {ver.name} ({state_name})")
    
    
    idx_str = log_input("请选择要启动的版本序号: ").strip()
    try:
        idx = int(idx_str)
        if idx < 1 or idx > len(versions):
            log_error("无效的序号")
            return
        
        instance = versions[idx - 1]
    except ValueError:
        log_error("请输入有效的数字")
        return
    
    # 检查账号
    account = account_mgr.get_active_account()
    if not account:
        log_warn("未登录账号，将使用离线模式")
        import uuid
        account = {
            "type": "legacy",
            "name": "Player",
            "uuid": str(uuid.uuid4()),
            "access_token": str(uuid.uuid4()),
        }
    
    # 获取 Java
    needed_java = instance.get_java_version()
    log_info(f"该版本需要 Java {needed_java}")
    java_path = config.get("Launch", "JavaPath", "")
    # 如果已配置 Java，检查版本是否达标且不过高
    if java_path:
        from launcher.launch import _get_java_major_version
        detected = _get_java_major_version(java_path)
        if detected < needed_java:
            log_warn(f"配置的 Java {detected} 不满足需求 (需要 Java {needed_java})，重新搜索...")
            java_path = ""
        elif detected > needed_java + 1:
            log_warn(f"配置的 Java {detected} 版本过高 (建议 Java {needed_java})，寻找更匹配的版本...")
            java_path = ""
    if not java_path:
        log_info("正在自动检测 Java...")
        java_path = find_java(min_version=needed_java)
        if java_path:
            config.set("Launch", "JavaPath", java_path)
    
    if not java_path:
        log_error(f"未找到 Java {needed_java}+，请在设置中配置 Java 路径")
        return
    
    # 构建启动选项
    options = LaunchOptions()
    options.instance = instance
    options.account = account
    options.java_path = java_path
    options.min_memory = config.get_int("Launch", "MinMemory", 1024)
    options.max_memory = config.get_int("Launch", "MaxMemory", 2048)
    options.jvm_args = config.get("Launch", "JvmArgs", "")
    options.window_width = config.get_int("Launch", "WindowWidth", 854)
    options.window_height = config.get_int("Launch", "WindowHeight", 480)
    options.version_isolation = config.get_bool("Launch", "VersionIsolation", False)
    options.server_ip = config.get("Launch", "AutoConnectServer", "")
    
    # 始终开启版本隔离
    options.version_isolation = True

    log_info("开始启动...")
    
    try:
        launch_minecraft(options)
    except Exception as e:
        log_error(f"启动失败: {e}")


def handle_install_version(config: ConfigManager, mc_folder: MinecraftFolder):
    """处理安装 Minecraft 版本"""
    
    log_info("正在获取可用版本列表...")
    manifest = net_get_manifest()
    if not manifest:
        log_error("获取版本列表失败，请检查网络连接")
        return
    
    versions = get_available_minecraft_versions(manifest)
    
    # 显示版本列表
    releases = [v for v in versions if v.get("type") == "release"]
    snapshots = [v for v in versions if v.get("type") == "snapshot"]
    
    log_info(f"最新正式版: {releases[0].get('id', '') if releases else 'N/A'}")
    log_info(f"最新快照: {snapshots[0].get('id', '') if snapshots else 'N/A'}")
    
    
    # 显示最近的版本
    log_info("最近正式版:")
    for v in releases[:10]:
        log_info(f"  - {v.get('id', '')} ({v.get('releaseTime', '')[:10]})")
    
    
    log_info("最近快照:")
    for v in snapshots[:5]:
        log_info(f"  - {v.get('id', '')} ({v.get('releaseTime', '')[:10]})")
    
    
    version_id = log_input("请输入要安装的版本号 (如 1.20.1): ").strip()
    if not version_id:
        log_error("版本号不能为空")
        return
    
    log_info(f"正在安装 Minecraft {version_id}...")
    
    # 询问是否使用镜像
    use_mirror = log_input("是否使用 BMCLAPI 镜像加速? (Y/n): ").strip().lower()
    use_mirror = use_mirror != "n"
    
    if install_minecraft_version(version_id, mc_folder, use_mirror=use_mirror):
        log_success(f"Minecraft {version_id} 安装完成!")
    else:
        log_error(f"Minecraft {version_id} 安装失败")


def handle_install_modpack(config: ConfigManager, mc_folder: MinecraftFolder):
    """处理安装整合包"""
    zip_path = log_input("请输入整合包文件路径: ").strip()
    if not zip_path:
        log_error("文件路径不能为空")
        return
    
    if not os.path.exists(zip_path):
        log_error(f"文件不存在: {zip_path}")
        return
    
    if not zip_path.endswith((".zip", ".mrpack")):
        log_warn("文件可能不是有效的整合包格式（需要 .zip 或 .mrpack）")
    
    instance_name = log_input("请输入实例名称 (留空自动生成): ").strip()
    if not instance_name:
        instance_name = None
    
    log_info("正在安装整合包，请稍候...")
    
    if install_modpack(zip_path, mc_folder, instance_name):
        log_success("整合包安装完成!")
    else:
        log_error("整合包安装失败")


def handle_manage_versions(mc_folder: MinecraftFolder):
    # 处理版本管理
    versions = mc_folder.scan_versions()

    
    if not versions:
        log_warn("未找到任何 Minecraft 版本")
        log_info("请先安装版本或整合包")
        return
    
    log_info(f"共找到 {len(versions)} 个版本:")
    for i, ver in enumerate(versions, 1):
        state_name = ver.get_state_name()
        has_jar = "False" if os.path.exists(ver.jar_path) else "True"
        log_info(INFO, f"[{i}] {ver.name} JAR:{has_jar}")
    log_info("[d] 删除版本  [0] 返回")
    choice = log_input("请选择操作: ").strip().lower()
    
    if choice.startswith("d"):
        idx_str = choice[1:].strip()
        if not idx_str:
            idx_str = log_input("请输入要删除的版本序号 (如 123): ").strip()
        indices = []
        for ch in idx_str:
            try:
                indices.append(int(ch))
            except ValueError:
                pass
        if not indices:
            log_error("请输入有效的序号")
            return
        names = []
        import shutil
        for idx in indices:
            real_idx = idx - 1
            if 0 <= real_idx < len(versions):
                ver = versions[real_idx]
                if os.path.exists(ver.path_version):
                    shutil.rmtree(ver.path_version, ignore_errors=True)
                    names.append(ver.name)
                    log_success(f"已删除版本: {ver.name}")
                else:
                    log_warn(f"版本目录不存在: {ver.name}")
            else:
                log_warn(f"无效的序号: {idx}")
        if names:
            log_info(f"共删除 {len(names)} 个版本")


def handle_settings(config: ConfigManager):
    # 处理设置
    while True:
        java_path = config.get("Launch", "JavaPath", "")
        min_mem = config.get_int("Launch", "MinMemory", 1024)
        max_mem = config.get_int("Launch", "MaxMemory", 2048)
        game_dir = config.get("Launch", "GameDirectory", DOT_MINECRAFT)
        version_iso = config.get_bool("Launch", "VersionIsolation", False)
        jvm_args = config.get("Launch", "JvmArgs", "")
        window_w = config.get_int("Launch", "WindowWidth", 854)
        window_h = config.get_int("Launch", "WindowHeight", 480)
        server_ip = config.get("Launch", "AutoConnectServer", "")
        
        dl_cfg = config.get_download_config()
        max_threads = int(dl_cfg.get("max_threads", 32))
        max_retries = int(dl_cfg.get("max_retries", 3))
        timeout = int(dl_cfg.get("timeout", 60))
        
        log_info(INFO, f"[1] Java 路径: {java_path or '自动检测'}")
        log_info(INFO, f"[2] 最小内存: {min_mem} MB")
        log_info(INFO, f"[3] 最大内存: {max_mem} MB")
        log_info(INFO, f"[4] 游戏目录: {game_dir}")
        log_info(INFO, f"[5] 版本隔离: {'开启' if version_iso else '关闭'}")
        log_info(INFO, f"[6] 额外 JVM 参数: {jvm_args or '无'}")
        log_info(INFO, f"[7] 窗口大小: {window_w}x{window_h}")
        log_info(INFO, f"[8] 自动进服: {server_ip or '未设置'}")
        log_info(INFO, f"[9] 下载线程数: {max_threads}")
        log_info(INFO, f"[10] 下载重试次数: {'无限' if max_retries == 0 else max_retries}")
        log_info(INFO, f"[11] 下载超时: {timeout} 秒")
        ms_client_id = config.get("login", "microsoft_client_id", "")
        log_info(INFO, f"[12] 微软 OAuth Client ID: {ms_client_id or '未设置'}")
        log_info(INFO, "[0] 返回主菜单")

        
        choice = log_input("请选择操作: ").strip()
        
        if choice == "0":
            break
        elif choice == "1":
            path = log_input("Java 路径 (留空自动检测): ").strip()
            config.set("Launch", "JavaPath", path)
            log_success("Java 路径已更新")
        elif choice == "2":
            val = log_input(f"最小内存 (MB) [{min_mem}]: ").strip()
            if val:
                try:
                    config.set("Launch", "MinMemory", str(int(val)))
                    log_success("最小内存已更新")
                except:
                    log_error("请输入有效的数字")
        elif choice == "3":
            val = log_input(f"最大内存 (MB) [{max_mem}]: ").strip()
            if val:
                try:
                    config.set("Launch", "MaxMemory", str(int(val)))
                    log_success("最大内存已更新")
                except:
                    log_error("请输入有效的数字")
        elif choice == "4":
            path = log_input(f"游戏目录 [{game_dir}]: ").strip()
            if path:
                config.set("Launch", "GameDirectory", path)
                log_success("游戏目录已更新")
        elif choice == "5":
            config.set("Launch", "VersionIsolation", "false" if version_iso else "true")
            log_success(f"版本隔离已切换为 {'开启' if not version_iso else '关闭'}")
        elif choice == "6":
            args = log_input("额外 JVM 参数: ").strip()
            config.set("Launch", "JvmArgs", args)
            log_success("JVM 参数已更新")
        elif choice == "7":
            w = log_input(f"窗口宽度 [{window_w}]: ").strip()
            h = log_input(f"窗口高度 [{window_h}]: ").strip()
            if w:
                try:
                    config.set("Launch", "WindowWidth", str(int(w)))
                except:
                    log_error("请输入有效的数字")
            if h:
                try:
                    config.set("Launch", "WindowHeight", str(int(h)))
                except:
                    log_error("请输入有效的数字")
            log_success("窗口大小已更新")
        elif choice == "8":
            ip = log_input("自动进服地址 (留空清除): ").strip()
            config.set("Launch", "AutoConnectServer", ip)
            log_success("自动进服地址已更新")
        elif choice == "9":
            val = log_input(f"下载线程数 [{max_threads}] (0=自动): ").strip()
            if val:
                try:
                    n = max(int(val), 1)
                    config.set_download_config(max_threads=n)
                    log_success(f"下载线程数已设为 {n}")
                except:
                    log_error("请输入有效的数字")
        elif choice == "10":
            val = log_input(f"下载重试次数 [{max_retries}] (0=无限): ").strip()
            if val:
                try:
                    n = max(int(val), 0)
                    config.set_download_config(max_retries=n)
                    log_success(f"下载重试次数已设为 {'无限' if n == 0 else n}")
                except:
                    log_error("请输入有效的数字")
        elif choice == "11":
            val = log_input(f"下载超时 (秒) [{timeout}]: ").strip()
            if val:
                try:
                    n = max(int(val), 5)
                    config.set_download_config(timeout=n)
                    log_success(f"下载超时已设为 {n} 秒")
                except:
                    log_error("请输入有效的数字")
        elif choice == "12":
            val = log_input("微软 OAuth Client ID (留空清除): ").strip()
            config.set("login", "microsoft_client_id", val)
            if val:
                log_success("微软 OAuth Client ID 已更新")
            else:
                log_success("已清除微软 OAuth Client ID")
        else:
            log_warn("无效的选项")
        



if __name__ == "__main__":
    main()
