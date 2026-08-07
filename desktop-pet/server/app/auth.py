from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import tempfile
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional


USERNAME_PATTERN = re.compile(r"^[\w.-]{3,40}$", re.UNICODE)
PASSWORD_MIN_LENGTH = 8
SESSION_COOKIE = "desktop_pet_session"
SESSION_TTL_DAYS = 30
PBKDF2_ITERATIONS = 240_000


class AuthStoreError(ValueError):
    """A user-facing authentication or account validation error."""


class UserStore:
    """Small filesystem-backed account, session, and per-user AI store.

    This application intentionally remains deployable as a single container,
    so the account layer uses the same mounted data directory as jobs. The
    password is never stored in plaintext and session tokens are stored as
    hashes, which means a leaked users.json cannot be used as a live session.
    """

    def __init__(self, data_dir: Path, default_ai_factory: Callable[[], Dict[str, Any]]):
        self.data_dir = data_dir
        self.users_file = data_dir / "users.json"
        self.sessions_file = data_dir / "sessions.json"
        self.users_dir = data_dir / "users"
        self.default_ai_factory = default_ai_factory
        self._lock = threading.Lock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)

    def register(self, username: str, password: str) -> Dict[str, Any]:
        username = self._validate_username(username)
        self._validate_password(password)
        with self._lock:
            users = self._read_users()
            if any(str(item.get("username", "")).casefold() == username.casefold() for item in users):
                raise AuthStoreError("用户名已存在")
            user = {
                "id": uuid.uuid4().hex,
                "username": username,
                "role": "user",
                "password_hash": _hash_password(password),
                "created_at": _utc_now(),
            }
            users.append(user)
            self._write_json(self.users_file, users)
            self._ensure_ai_file(user["id"])
            return self.public_user(user)

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        username = str(username or "").strip()
        with self._lock:
            users = self._read_users()
            user = next(
                (item for item in users if str(item.get("username", "")).casefold() == username.casefold()),
                None,
            )
            if not user or not _verify_password(password, str(user.get("password_hash", ""))):
                raise AuthStoreError("用户名或密码错误")
            return self.public_user(user)

    def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        token_hash = _hash_token(token)
        expires_at = datetime.utcnow() + timedelta(days=SESSION_TTL_DAYS)
        with self._lock:
            sessions = self._read_sessions()
            self._purge_expired(sessions)
            sessions[token_hash] = {
                "user_id": user_id,
                "expires_at": expires_at.isoformat() + "Z",
            }
            self._write_json(self.sessions_file, sessions)
        return token

    def get_session_user(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        token_hash = _hash_token(token)
        with self._lock:
            sessions = self._read_sessions()
            entry = sessions.get(token_hash)
            if not entry or _expired(entry.get("expires_at")):
                if entry:
                    sessions.pop(token_hash, None)
                    self._write_json(self.sessions_file, sessions)
                return None
            user = self._find_user(entry.get("user_id"))
            return self.public_user(user) if user else None

    def delete_session(self, token: Optional[str]) -> None:
        if not token:
            return
        token_hash = _hash_token(token)
        with self._lock:
            sessions = self._read_sessions()
            if token_hash in sessions:
                sessions.pop(token_hash, None)
                self._write_json(self.sessions_file, sessions)

    def public_user(self, user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not user:
            return {}
        return {
            "id": str(user.get("id", "")),
            "username": str(user.get("username", "")),
            "role": str(user.get("role", "user")),
            "created_at": user.get("created_at", ""),
        }

    def get_ai_config(self, user_id: str) -> Dict[str, Any]:
        path = self._ai_file(user_id)
        with self._lock:
            values = self.default_ai_factory()
            if path.exists():
                try:
                    stored = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    stored = {}
                if isinstance(stored, dict):
                    values.update(stored)
            self._write_json(path, values)
            return values

    def save_ai_config(self, user_id: str, values: Dict[str, Any]) -> None:
        with self._lock:
            self._write_json(self._ai_file(user_id), values)

    def _find_user(self, user_id: Any) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        return next((item for item in self._read_users() if item.get("id") == user_id), None)

    def _read_users(self):
        values = self._read_json(self.users_file, [])
        if isinstance(values, dict):
            values = values.get("users", [])
        return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []

    def _read_sessions(self):
        values = self._read_json(self.sessions_file, {})
        return {
            key: value
            for key, value in values.items()
            if isinstance(key, str) and isinstance(value, dict)
        } if isinstance(values, dict) else {}

    @staticmethod
    def _read_json(path: Path, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @staticmethod
    def _write_json(path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=path.stem + "-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            temporary = Path(handle.name)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(path)

    def _ensure_ai_file(self, user_id: str) -> None:
        path = self._ai_file(user_id)
        if not path.exists():
            self._write_json(path, self.default_ai_factory())

    def _ai_file(self, user_id: str) -> Path:
        if not re.fullmatch(r"[a-f0-9]{16,64}", str(user_id or "")):
            raise AuthStoreError("非法用户 ID")
        return self.users_dir / str(user_id) / "settings.json"

    @staticmethod
    def _validate_username(value: str) -> str:
        username = str(value or "").strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise AuthStoreError("用户名需为 3-40 位字母、数字、下划线、短横线或点号")
        return username

    @staticmethod
    def _validate_password(value: str) -> None:
        if not isinstance(value, str) or len(value) < PASSWORD_MIN_LENGTH:
            raise AuthStoreError("密码至少需要 8 位")

    @staticmethod
    def _purge_expired(sessions: Dict[str, Any]) -> None:
        for token_hash, entry in list(sessions.items()):
            if not isinstance(entry, dict) or _expired(entry.get("expires_at")):
                sessions.pop(token_hash, None)


def _utc_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _expired(value: Any) -> bool:
    if not value:
        return True
    try:
        timestamp = str(value).rstrip("Z")
        try:
            parsed = datetime.fromisoformat(timestamp)
        except AttributeError:
            parsed = datetime.strptime(timestamp.split(".", 1)[0], "%Y-%m-%dT%H:%M:%S")
        return datetime.utcnow() >= parsed
    except (TypeError, ValueError):
        return True


def _hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(raw_salt)
        expected = bytes.fromhex(raw_digest)
    except (AttributeError, TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", str(password).encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
