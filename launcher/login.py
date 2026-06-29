# 登录/认证模块
#
# 支持:
#   - 离线登录 (Legacy) - 本地用户名
#   - 微软登录 (Microsoft OAuth) - 完整 OAuth 流程
#   - 第三方 Yggdrasil 认证 (Authlib-Injector)

import uuid
import hashlib
import json
import urllib.request
import urllib.parse
import urllib.error
from .logger import log_info, log_debug, log_warn, log_error, log_success, log_request, log_input
from .network import net_request

# 登录类型
LOGIN_TYPE_LEGACY = 0    # 离线登录
LOGIN_TYPE_NIDE = 2      # 统一通行证
LOGIN_TYPE_AUTH = 3      # Authlib-Injector
LOGIN_TYPE_MS = 5        # 微软登录

# 微软 OAuth 配置（可从 config.json 覆盖）
_DEFAULT_CLIENT_ID = "0c0e9025-67b7-4da6-bc22-64d6ca502f80"


def _get_client_id() -> str:
    """获取微软 OAuth Client ID，优先读取配置。"""
    try:
        from .config import CONFIG_JSON
        import json
        with open(CONFIG_JSON, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        cid = cfg.get("login", {}).get("microsoft_client_id", "")
        if cid:
            return cid
    except Exception:
        pass
    return _DEFAULT_CLIENT_ID  # PCL 使用的 Client ID


def _form_post(url: str, data: dict) -> tuple[int, dict | None]:
    """以 application/x-www-form-urlencoded 格式发送 POST 请求（微软 OAuth 用）。"""
    payload = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("User-Agent", "CMD-Minecraft-Launcher/1.0.0")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw.decode("utf-8"))
            except Exception:
                return resp.status, None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
            log_warn(f"微软 OAuth 错误: {body.get('error_description', e.reason)}")
        except Exception:
            log_warn(f"微软 OAuth HTTP {e.code}: {e.reason}")
        return e.code, None
    except Exception as e:
        log_error(f"请求微软 OAuth 失败: {e}")
        return 0, None


def _generate_uuid_from_name(name: str) -> str:
    # 根据用户名生成 UUID（离线模式）
    hash_obj = hashlib.md5(name.encode("utf-8"))
    hash_hex = hash_obj.hexdigest()
    # 按 UUID 格式: 8-4-4-4-12
    formatted = f"{hash_hex[0:8]}-{hash_hex[8:12]}-{hash_hex[12:16]}-{hash_hex[16:20]}-{hash_hex[20:32]}"
    return formatted


def login_offline(username: str) -> dict | None:
    # 离线登录
    #
    # 参数:
    #     username: 玩家用户名
    #
    # 返回:
    #     {
    #         "type": "legacy",
    #         "name": str,
    #         "uuid": str,
    #         "access_token": str,
    #         "client_token": str,
    #     }
    log_info(f"执行离线登录...")
    
    if not username or len(username.strip()) == 0:
        log_error("用户名不能为空")
        return None
    
    username = username.strip()
    
    # 生成 UUID
    player_uuid = _generate_uuid_from_name(username)
    
    result = {
        "type": "legacy",
        "name": username,
        "uuid": player_uuid,
        "access_token": player_uuid,  # 离线模式 access_token = uuid
        "client_token": str(uuid.uuid4()),
    }
    
    log_success(f"离线登录成功!")
    log_info(f"用户名: {username}")
    log_info(f"UUID: {player_uuid}")
    
    return result


def _ms_device_code_login(client_id: str) -> dict | None:
    # 微软设备码登录
    #
    # 流程:
    # 1. 获取设备码
    # 2. 提示用户在浏览器中打开链接并输入代码
    # 3. 轮询等待用户完成认证
    #
    # 返回: OAuth Token 响应
    # 步骤 1: 获取设备码
    log_info("正在请求微软设备码...")
    
    code, data = _form_post(
        "https://login.microsoftonline.com/consumers/oauth2/v2.0/devicecode",
        {
            "client_id": client_id,
            "scope": "XboxLive.signin offline_access"
        }
    )
    
    if code != 200 or not data:
        log_error("获取设备码失败")
        return None
    if not isinstance(data, dict):
        log_error("获取设备码失败: 返回格式异常")
        return None
    
    device_code = data.get("device_code", "")
    user_code = data.get("user_code", "")
    verification_uri = data.get("verification_uri", "")
    interval = data.get("interval", 5)
    expires_in = data.get("expires_in", 900)
    
    log_request(f"请在浏览器中打开以下链接:")
    log_request(f"链接: {verification_uri}")
    log_request(f"代码: {user_code}")
    log_request(f"有效期: {expires_in} 秒")
    log_request(f"完成后按回车继续...")
    
    input()
    
    # 步骤 2: 轮询等待用户完成
    import time
    elapsed = 0
    
    while elapsed < expires_in:
        time.sleep(interval)
        elapsed += interval
        
        code, data = _form_post(
            "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
            {
                "client_id": client_id,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code
            }
        )
        
        if code == 200 and isinstance(data, dict):
            if "access_token" in data:
                log_success("设备码认证成功!")
                return data
        
        if isinstance(data, dict) and "error" in data:
            error = data["error"]
            if error == "authorization_declined":
                log_error("用户拒绝了授权")
                return None
            elif error == "expired_token":
                log_error("设备码已过期")
                return None
            # authorization_pending 继续等待
    
    log_error("设备码认证超时")
    return None


def _ms_xbl_auth(access_token: str) -> dict | None:
    # 步骤 2: OAuth Token → XBL Token
    log_info("正在获取 Xbox Live 令牌...")
    
    code, data = net_request(
        "https://user.auth.xboxlive.com/user/authenticate",
        method="POST",
        headers={"x-xbl-contract-version": "1"},
        post_data={
            "Properties": {
                "AuthMethod": "RPS",
                "SiteName": "user.auth.xboxlive.com",
                "RpsTicket": f"d={access_token}"
            },
            "RelyingParty": "http://auth.xboxlive.com",
            "TokenType": "JWT"
        }
    )
    
    if code != 200 or not data:
        log_error("获取 XBL 令牌失败")
        return None
    if not isinstance(data, dict):
        log_error("获取 XBL 令牌失败: 返回格式异常")
        return None
    
    log_success("XBL 令牌获取成功")
    return data


def _ms_xsts_auth(xbl_token: str) -> dict | None:
    # 步骤 3: XBL Token → XSTS Token
    log_info("正在获取 XSTS 令牌...")
    
    code, data = net_request(
        "https://xsts.auth.xboxlive.com/xsts/authorize",
        method="POST",
        headers={"x-xbl-contract-version": "1"},
        post_data={
            "Properties": {
                "SandboxId": "RETAIL",
                "UserTokens": [xbl_token]
            },
            "RelyingParty": "rp://api.minecraftservices.com/",
            "TokenType": "JWT"
        }
    )
    
    if code != 200 or not data:
        log_error("获取 XSTS 令牌失败")
        return None
    if not isinstance(data, dict):
        log_error("获取 XSTS 令牌失败: 返回格式异常")
        return None
    
    log_success("XSTS 令牌获取成功")
    return data


def _ms_mc_auth(xbl_token: str, uhs: str) -> dict | None:
    # 步骤 4: XSTS Token → Minecraft AccessToken
    log_info("正在获取 Minecraft 令牌...")
    
    code, data = net_request(
        "https://api.minecraftservices.com/authentication/login_with_xbox",
        method="POST",
        post_data={
            "identityToken": f"XBL3.0 x={uhs};{xbl_token}"
        }
    )
    
    if code != 200 or not data:
        log_error("获取 Minecraft 令牌失败")
        return None
    if not isinstance(data, dict):
        log_error("获取 Minecraft 令牌失败: 返回格式异常")
        return None
    
    log_success("Minecraft 令牌获取成功")
    return data


def _ms_check_ownership(access_token: str) -> bool:
    # 步骤 5: 验证是否拥有 Minecraft
    log_info("正在验证游戏所有权...")
    
    code, data = net_request(
        "https://api.minecraftservices.com/entitlements/mcstore",
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    
    if code != 200 or not data:
        log_error("验证游戏所有权失败")
        return False
    if not isinstance(data, dict):
        log_error("验证游戏所有权失败: 返回格式异常")
        return False
    
    # 检查是否有 Minecraft 许可
    items = data.get("items", [])
    has_minecraft = any(
        item.get("name") == "product_minecraft" 
        or item.get("name") == "game_minecraft"
        for item in items
    )
    
    if has_minecraft:
        log_success("已验证: 拥有 Minecraft")
        return True
    else:
        log_warn("未检测到 Minecraft 所有权（可能通过 XGP 游玩）")
        return True  # XGP 用户可能没有 product_minecraft


def _ms_get_profile(access_token: str) -> dict | None:
    # 步骤 6: 获取玩家资料
    log_info("正在获取玩家资料...")
    
    code, data = net_request(
        "https://api.minecraftservices.com/minecraft/profile",
        method="GET",
        headers={"Authorization": f"Bearer {access_token}"}
    )
    
    if code != 200 or not data:
        log_error("获取玩家资料失败")
        return None
    if not isinstance(data, dict):
        log_error("获取玩家资料失败: 返回格式异常")
        return None
    
    log_success("玩家资料获取成功")
    return data


def login_microsoft() -> dict | None:
    # 微软登录
    #
    # 完整 OAuth 流程:
    # 1. 获取设备码 → 用户浏览器授权
    # 2. OAuth Token → XBL Token
    # 3. XBL Token → XSTS Token (含 UHS)
    # 4. XSTS → Minecraft AccessToken
    # 5. 验证游戏所有权
    # 6. 获取玩家 Profile
    #
    # 返回:
    #     {
    #         "type": "microsoft",
    #         "name": str,
    #         "uuid": str,
    #         "access_token": str,
    #         "refresh_token": str,
    #         "profile_json": dict,
    #     }
    log_info("执行微软登录...")

    # 检查是否有配置 Client ID，没有则提示输入
    client_id = _get_client_id()
    if client_id == _DEFAULT_CLIENT_ID:
        log_warn("默认 OAuth Client ID 可能已失效")
        inp = log_input("输入微软 OAuth Client ID (留空使用默认): ")
        if inp.strip():
            client_id = inp.strip()
            # 保存到配置
            try:
                from .config import CONFIG_JSON
                with open(CONFIG_JSON, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if "login" not in cfg:
                    cfg["login"] = {}
                cfg["login"]["microsoft_client_id"] = client_id
                with open(CONFIG_JSON, "w", encoding="utf-8") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                log_success("Client ID 已保存到配置")
            except Exception:
                pass

    # 步骤 1: 设备码登录
    oauth = _ms_device_code_login(client_id)
    if not oauth:
        return None
    
    access_token: str | None = oauth.get("access_token")
    refresh_token = oauth.get("refresh_token", "")
    
    if not access_token:
        log_error("未获取到 access_token")
        return None
    
    # 步骤 2: XBL 认证
    xbl = _ms_xbl_auth(access_token)
    if not xbl:
        return None
    
    xbl_token = xbl.get("Token", "")
    
    # 步骤 3: XSTS 认证
    xsts = _ms_xsts_auth(xbl_token)
    if not xsts:
        return None
    
    xsts_token = xsts.get("Token", "")
    uhs = ""
    if "DisplayClaims" in xsts and "xui" in xsts["DisplayClaims"]:
        uhs = xsts["DisplayClaims"]["xui"][0].get("uhs", "")
    
    # 步骤 4: Minecraft 认证
    mc = _ms_mc_auth(xsts_token, uhs)
    if not mc:
        return None
    
    mc_access_token = mc.get("access_token", "")
    
    # 步骤 5: 验证所有权
    if not _ms_check_ownership(mc_access_token):
        log_warn("游戏所有权验证未通过，但尝试继续...")
    
    # 步骤 6: 获取资料
    profile = _ms_get_profile(mc_access_token)
    if not profile:
        log_error("获取玩家资料失败，登录不完全")
        return {
            "type": "microsoft",
            "name": "未知玩家",
            "uuid": "",
            "access_token": mc_access_token,
            "refresh_token": refresh_token,
            "profile_json": {},
        }
    
    player_name = profile.get("name", "未知玩家")
    player_uuid = profile.get("id", "")
    # 格式化 UUID
    if player_uuid and len(player_uuid) == 32:
        player_uuid = f"{player_uuid[0:8]}-{player_uuid[8:12]}-{player_uuid[12:16]}-{player_uuid[16:20]}-{player_uuid[20:32]}"
    
    result = {
        "type": "microsoft",
        "name": player_name,
        "uuid": player_uuid,
        "access_token": mc_access_token,
        "refresh_token": refresh_token,
        "profile_json": profile,
    }
    
    log_success(f"微软登录成功!")
    log_info(f"  玩家: {player_name}")
    log_info(f"  UUID: {player_uuid}")
    
    return result


def login_yggdrasil(server_url: str, username: str, password: str | None = None) -> dict | None:
    # 第三方 Yggdrasil 认证登录
    #
    # 参数:
    #     server_url: 认证服务器地址（如 https://auth.example.com）
    #     username: 用户名/邮箱
    #     password: 密码（为 None 时尝试令牌刷新）
    #
    # 返回:
    #     {
    #         "type": "auth",
    #         "name": str,
    #         "uuid": str,
    #         "access_token": str,
    #         "client_token": str,
    #         "server_url": str,
    #     }
    log_info(f"执行第三方 Yggdrasil 登录...")
    log_info(f"  服务器: {server_url}")
    log_info(f"  用户名: {username}")
    
    # 确保 URL 格式正确
    base_url = server_url.rstrip("/")
    
    if password:
        # 使用密码登录 (authenticate)
        log_info("正在使用密码登录...")
        
        code, data = net_request(
            f"{base_url}/authserver/authenticate",
            method="POST",
            post_data={
                "agent": {"name": "Minecraft", "version": 1},
                "username": username,
                "password": password,
                "requestUser": True,
            }
        )
        
        if code != 200 or not data:
            log_error("Yggdrasil 登录失败")
            if isinstance(data, dict) and "errorMessage" in data:
                log_error(f"  错误: {data['errorMessage']}")
            return None
        if not isinstance(data, dict):
            log_error("Yggdrasil 登录失败: 返回格式异常")
            return None
        
        available_profiles = data.get("availableProfiles", [])
        
        if len(available_profiles) == 0:
            log_error("该账号没有可用的游戏角色")
            return None
        
        # 如果有多个角色，让用户选择
        selected_profile = available_profiles[0]
        if len(available_profiles) > 1:
            log_request("检测到多个游戏角色，请选择:")
            for i, profile in enumerate(available_profiles):
                log_request(f"  [{i}] {profile.get('name', '未知')} ({profile.get('id', '')})")
            
            try:
                choice = int(log_input("请输入序号: ").strip())
                if 0 <= choice < len(available_profiles):
                    selected_profile = available_profiles[choice]
                else:
                    log_warn("序号无效，使用第一个角色")
            except:
                log_warn("输入无效，使用第一个角色")
        
        access_token = data.get("accessToken", "")
        client_token = data.get("clientToken", "")
        profile_id = selected_profile.get("id", "")
        profile_name = selected_profile.get("name", "")
        
        # 格式化 UUID
        if profile_id and len(profile_id) == 32:
            profile_id = f"{profile_id[0:8]}-{profile_id[8:12]}-{profile_id[12:16]}-{profile_id[16:20]}-{profile_id[20:32]}"
        
        result = {
            "type": "auth",
            "name": profile_name,
            "uuid": profile_id,
            "access_token": access_token,
            "client_token": client_token,
            "server_url": server_url,
            "username": username,
        }
        
        log_success(f"Yggdrasil 登录成功!")
        log_info(f"  角色: {profile_name}")
        log_info(f"  UUID: {profile_id}")
        
        return result
    else:
        log_error("第三方登录需要密码")
        return None


def refresh_microsoft_token(refresh_token: str, client_id: str | None = None) -> dict | None:
    # 刷新微软登录令牌
    if not client_id:
        client_id = _get_client_id()
    log_info("正在刷新微软令牌...")
    
    code, data = _form_post(
        "https://login.live.com/oauth20_token.srf",
        {
            "client_id": client_id,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": "XboxLive.signin offline_access"
        }
    )
    
    if code != 200 or not data:
        log_warn("刷新令牌失败，需要重新登录")
        return None
    if not isinstance(data, dict):
        log_warn("刷新令牌失败: 返回格式异常")
        return None
    
    log_success("令牌刷新成功")
    return data
