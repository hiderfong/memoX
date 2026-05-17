"""Auth router"""
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from auth import _get_auth_from_request, get_auth_manager

router = APIRouter(prefix="/api/auth", tags=["auth"])

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
@limiter.limit("10/minute")
async def login(request: Request, login_req: LoginRequest) -> dict:
    """用户登录，返回 Bearer Token"""
    auth = get_auth_manager()
    token = auth.login(login_req.username, login_req.password)
    if not token:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    user_info = auth.get_user_info(token)
    return {
        "token": token,
        "token_type": "Bearer",
        "user": user_info,
    }


@router.post("/logout")
async def logout(request: Request) -> dict:
    """用户登出，吊销 Token（需携带有效 Token）"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    auth = _get_auth_from_request(request)
    if not token or not auth.validate_token(token):
        raise HTTPException(status_code=401, detail="未登录或 Token 已过期")
    auth.logout(token)
    return {"success": True}


@router.get("/me")
async def me(request: Request) -> dict:
    """获取当前登录用户信息"""
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    user_info = _get_auth_from_request(request).get_user_info(token)
    if not user_info:
        raise HTTPException(status_code=401, detail="未登录")
    return user_info
