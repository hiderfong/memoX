"""开发期认证模块 - Bearer Token + PBKDF2 密码哈希"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass, field


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
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    return _auth_manager


def init_auth(users: list[dict]) -> AuthManager:
    """初始化认证，users: [{"username":..,"password":..,"role":..,"display_name":..}]"""
    global _auth_manager
    _auth_manager = AuthManager()
    for u in users:
        _auth_manager.add_user(
            username=u["username"],
            password=u["password"],
            role=u.get("role", "user"),
            display_name=u.get("display_name", u["username"]),
        )
    return _auth_manager
