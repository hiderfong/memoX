"""开发期认证模块 - Bearer Token + PBKDF2 密码哈希"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Security, status


@dataclass
class User:
    username: str
    password_hash: str   # "salt:hex_hash"
    role: str = "user"
    display_name: str = ""


class AuthManager:
    """内存用户存储 + Token 管理（开发模式）"""

    TOKEN_TTL = 24 * 3600  # token 有效期 24 小时
    PBKDF2_ITERATIONS = 100_000

    def __init__(self):
        self._users: dict[str, User] = {}
        self._tokens: dict[str, dict] = {}  # token -> {username, role, display_name, exp}

    # ── 密码 ──────────────────────────────────────────────────

    def _hash_password(self, password: str) -> str:
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), self.PBKDF2_ITERATIONS
        )
        return f"{salt}:{digest.hex()}"

    def _verify_password(self, password: str, stored: str) -> bool:
        try:
            salt, expected = stored.split(":", 1)
            digest = hashlib.pbkdf2_hmac(
                "sha256", password.encode(), salt.encode(), self.PBKDF2_ITERATIONS
            )
            return hmac.compare_digest(digest.hex(), expected)
        except Exception:
            return False

    # ── 用户管理 ──────────────────────────────────────────────

    def add_user(
        self,
        username: str,
        password: str,
        role: str = "user",
        display_name: str = "",
    ) -> User:
        user = User(
            username=username,
            password_hash=self._hash_password(password),
            role=role,
            display_name=display_name or username,
        )
        self._users[username] = user
        return user

    # ── 登录 / Token ──────────────────────────────────────────

    def login(self, username: str, password: str) -> str | None:
        """验证凭据，成功返回 token，失败返回 None"""
        user = self._users.get(username)
        if not user or not self._verify_password(password, user.password_hash):
            return None
        token = secrets.token_urlsafe(32)
        self._tokens[token] = {
            "username": user.username,
            "role": user.role,
            "display_name": user.display_name,
            "exp": time.time() + self.TOKEN_TTL,
        }
        return token

    def validate_token(self, token: str | None) -> dict | None:
        """验证 token 有效性，过期自动清除"""
        if not token or not isinstance(token, str):
            return None
        info = self._tokens.get(token)
        if not info:
            return None
        if time.time() > info["exp"]:
            del self._tokens[token]
            return None
        return info

    def logout(self, token: str) -> None:
        self._tokens.pop(token, None)

    def get_user_info(self, token: str) -> dict | None:
        info = self.validate_token(token)
        if not info:
            return None
        return {
            "username": info["username"],
            "role": info["role"],
            "display_name": info["display_name"],
        }

    def active_token_count(self) -> int:
        # 顺带清理过期 token
        now = time.time()
        expired = [t for t, v in self._tokens.items() if now > v["exp"]]
        for t in expired:
            del self._tokens[t]
        return len(self._tokens)


# ── 全局单例 ──────────────────────────────────────────────────

_auth_manager: AuthManager | None = None


def get_auth_manager() -> AuthManager:
    """返回 AuthManager 单例（优先从 FastAPI app.state 取，回退到模块级单例）。"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


def _get_auth_from_request(request: Request) -> AuthManager:
    """从 FastAPI request.app.state 取出 auth manager（用于 DI 覆盖测试）。"""
    try:
        app_state = getattr(request.app, "state", None)
        if app_state is not None and hasattr(app_state, "_auth_manager"):
            return app_state._auth_manager
    except Exception:
        pass
    return get_auth_manager()


def init_auth(users: list[dict], app_state=None) -> AuthManager:
    """初始化认证，users: [{"username":..,"password":..,"role":..,"display_name":..}]
    同时可接受 FastAPI app.state 用于后续 DI 覆盖。
    """
    global _auth_manager
    _auth_manager = AuthManager()
    for u in users:
        _auth_manager.add_user(
            username=u["username"],
            password=u["password"],
            role=u.get("role", "user"),
            display_name=u.get("display_name", u["username"]),
        )
    # 同时存到 app_state（如果传入了 FastAPI app实例）
    if app_state is not None:
        app_state._auth_manager = _auth_manager
    return _auth_manager


# ── 路由保护依赖 ──────────────────────────────────────────

@dataclass
class AuthUser:
    """从 Token 解析出的已认证用户（供 FastAPI 路由使用）"""
    username: str
    role: str
    display_name: str


def _extract_token(request: Request) -> str:
    """从请求中提取 Bearer token"""
    if request.url.path in ("/ws", "/ws/"):
        return request.query_params.get("token", "")
    auth_header = request.headers.get("Authorization", "")
    return auth_header.removeprefix("Bearer ").strip()


async def get_current_user(
    request: Request,
    auth: Annotated[AuthManager, Depends(_get_auth_from_request)],
) -> AuthUser:
    """FastAPI 依赖：从请求中获取当前登录用户"""
    token = _extract_token(request)
    info = auth.validate_token(token)
    if not info:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或 Token 已过期",
        )
    return AuthUser(
        username=info["username"],
        role=info["role"],
        display_name=info["display_name"],
    )


def require_role(*roles: str):
    """FastAPI Security 依赖：检查当前用户是否拥有指定角色之一"""
    async def checker(
        user: Annotated[AuthUser, Depends(get_current_user)],
    ) -> AuthUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要角色 {'/'.join(roles)}，当前用户角色为 '{user.role}'",
            )
        return user
    return Security(checker)
