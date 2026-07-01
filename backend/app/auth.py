from __future__ import annotations
import hashlib
import json
import secrets
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import HTTPException

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

USERS_FILE = DATA_DIR / "users.json"

_tokens: Dict[str, "User"] = {}


@dataclass
class User:
    username: str
    password_hash: str
    role: str
    enabled: bool = True
    security_question: str = ""
    security_answer_hash: str = ""


def hash_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _default_users() -> List[User]:
    return [
        User(
            username="admin",
            password_hash=hash_text("admin123"),
            role="admin",
            enabled=True,
            security_question="管理员默认账号",
            security_answer_hash=hash_text("admin123"),
        ),
        User(
            username="user",
            password_hash=hash_text("user123"),
            role="user",
            enabled=True,
            security_question="默认用户账号",
            security_answer_hash=hash_text("user123"),
        ),
    ]


def ensure_users_file():
    if not USERS_FILE.exists():
        save_users(_default_users())


def _user_from_dict(data: dict) -> User:
    return User(
        username=data.get("username", ""),
        password_hash=data.get("password_hash", ""),
        role=data.get("role", "user"),
        enabled=bool(data.get("enabled", True)),
        security_question=data.get("security_question", ""),
        security_answer_hash=data.get("security_answer_hash", ""),
    )


def load_users() -> List[User]:
    ensure_users_file()
    try:
        raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            users = _default_users()
            save_users(users)
            return users
        return [_user_from_dict(item) for item in raw]
    except Exception:
        users = _default_users()
        save_users(users)
        return users


def save_users(users: List[User]):
    USERS_FILE.write_text(
        json.dumps([asdict(u) for u in users], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_user(username: str) -> Optional[User]:
    for user in load_users():
        if user.username == username:
            return user
    return None


def sanitize_user(user: User) -> dict:
    return {
        "username": user.username,
        "role": user.role,
        "enabled": user.enabled,
        "security_question": user.security_question,
    }


def authenticate_user(username: str, password: str, role: Optional[str] = None) -> Optional[User]:
    user = get_user(username)
    if not user:
        return None
    if not user.enabled:
        return None
    if role and user.role != role:
        return None
    if user.password_hash != hash_text(password):
        return None
    return user


def verify_user(username: str, password: str, role: Optional[str] = None) -> Optional[User]:
    return authenticate_user(username, password, role)


def create_token(user: User) -> str:
    token = secrets.token_hex(24)
    _tokens[token] = user
    return token


def get_user_by_token(token: str) -> Optional[User]:
    return _tokens.get(token)


def remove_token(token: str):
    _tokens.pop(token, None)


def get_current_user(authorization: str | None) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="认证头格式错误")

    token = authorization.split(" ", 1)[1].strip()
    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="登录已失效，请重新登录")
    if not user.enabled:
        raise HTTPException(status_code=403, detail="账号已被禁用")
    return user


def require_admin(authorization: str | None) -> User:
    user = get_current_user(authorization)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def create_user(
    username: str,
    password: str,
    role: str,
    security_question: str,
    security_answer: str,
) -> User:
    users = load_users()

    username = (username or "").strip()
    password = password or ""
    role = (role or "user").strip()

    if not username:
        raise ValueError("用户名不能为空")
    if not password:
        raise ValueError("密码不能为空")
    if role not in {"admin", "user"}:
        raise ValueError("角色不合法")
    if get_user(username):
        raise ValueError("用户名已存在")

    user = User(
        username=username,
        password_hash=hash_text(password),
        role=role,
        enabled=True,
        security_question=security_question or "",
        security_answer_hash=hash_text(security_answer or ""),
    )
    users.append(user)
    save_users(users)
    return user


def register_user(username: str, password: str, security_question: str, security_answer: str) -> User:
    return create_user(username, password, "user", security_question, security_answer)


def get_security_question(username: str) -> str:
    user = get_user(username)
    if not user:
        raise ValueError("用户不存在")
    return user.security_question or ""


def reset_password_by_security_answer(username: str, answer: str, new_password: str):
    users = load_users()
    found = None

    for user in users:
        if user.username == username:
            found = user
            break

    if not found:
        raise ValueError("用户不存在")

    if found.security_answer_hash != hash_text(answer or ""):
        raise ValueError("安全答案错误")

    if not new_password:
        raise ValueError("新密码不能为空")

    found.password_hash = hash_text(new_password)
    save_users(users)


def admin_reset_password(username: str, new_password: str):
    users = load_users()
    found = None

    for user in users:
        if user.username == username:
            found = user
            break

    if not found:
        raise ValueError("用户不存在")

    if not new_password:
        raise ValueError("新密码不能为空")

    found.password_hash = hash_text(new_password)
    save_users(users)


def reset_user_password_by_admin(username: str, new_password: str):
    admin_reset_password(username, new_password)


def delete_user(username: str):
    if username == "admin":
        raise ValueError("默认管理员不能删除")

    users = load_users()
    new_users = [u for u in users if u.username != username]

    if len(new_users) == len(users):
        raise ValueError("用户不存在")

    save_users(new_users)


def update_user_role(username: str, role: str):
    if role not in {"admin", "user"}:
        raise ValueError("角色不合法")

    users = load_users()
    found = None

    for user in users:
        if user.username == username:
            found = user
            break

    if not found:
        raise ValueError("用户不存在")

    found.role = role
    save_users(users)


def update_user_enabled(username: str, enabled: bool):
    users = load_users()
    found = None

    for user in users:
        if user.username == username:
            found = user
            break

    if not found:
        raise ValueError("用户不存在")

    found.enabled = bool(enabled)
    save_users(users)