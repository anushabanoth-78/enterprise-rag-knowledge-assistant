import base64
import datetime as dt
import hashlib
import uuid
from dataclasses import dataclass

import bcrypt
import jwt
import redis
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db import repository
from app.db.models import User

log = structlog.get_logger()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
_redis = redis.Redis.from_url(settings.redis_url, socket_timeout=1, socket_connect_timeout=1)


# ---------------------------------------------------------------- passwords
def _prehash(password: str) -> bytes:
    # bcrypt only looks at the first 72 bytes. Pre-hashing makes every byte of a long passphrase count
    # and avoids the NUL-byte / truncation pitfalls.
    return base64.b64encode(hashlib.sha256(password.encode()).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


# ---------------------------------------------------------------- tokens
@dataclass
class TokenData:
    user_id: uuid.UUID
    jti: str
    exp: int


def create_access_token(user_id: uuid.UUID) -> str:
    exp = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": exp, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str) -> uuid.UUID:
    return decode_token_data(token).user_id


def decode_token_data(token: str) -> TokenData:
    try:
        p = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        return TokenData(uuid.UUID(p["sub"]), p.get("jti", ""), int(p["exp"]))
    except (jwt.PyJWTError, KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
                            headers={"WWW-Authenticate": "Bearer"})


def revoke_token(data: TokenData) -> None:
    """Logout: deny-list the token id until it would have expired anyway."""
    ttl = max(1, data.exp - int(dt.datetime.now(dt.timezone.utc).timestamp()))
    try:
        _redis.setex(f"jwt:deny:{data.jti}", ttl, "1")
    except redis.RedisError:
        log.error("token_revocation_failed_redis_unavailable")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Could not log out right now, try again")


def _is_revoked(jti: str) -> bool:
    if not jti:
        return False
    try:
        return bool(_redis.exists(f"jwt:deny:{jti}"))
    except redis.RedisError:
        # Availability over strictness for the deny-list only; tokens still expire on their own.
        log.warning("denylist_redis_unavailable")
        return False


# ---------------------------------------------------------------- dependencies
def get_token_data(token: str = Depends(oauth2_scheme)) -> TokenData:
    data = decode_token_data(token)
    if _is_revoked(data.jti):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked",
                            headers={"WWW-Authenticate": "Bearer"})
    return data


def get_current_user(data: TokenData = Depends(get_token_data),
                     db: Session = Depends(repository.get_db)) -> User:
    user = repository.get_user(db, data.user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def _hit(key: str, limit: int, window_s: int) -> None:
    """Fixed-window counter. Fails open if Redis is unavailable (logged)."""
    try:
        n = _redis.incr(key)
        if n == 1:
            _redis.expire(key, window_s + 10)
        if n > limit:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Rate limit exceeded, slow down",
                                headers={"Retry-After": str(window_s)})
    except redis.RedisError:
        log.warning("rate_limit_redis_unavailable")


def rate_limit(user: User = Depends(get_current_user)) -> User:
    _hit(f"rl:user:{user.id}:{int(dt.datetime.now().timestamp() // 60)}", settings.rate_limit_per_minute, 60)
    return user


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    """Brute-force protection for register/login, keyed by client IP."""
    _hit(f"rl:auth:{client_ip(request)}:{int(dt.datetime.now().timestamp() // 60)}",
         settings.auth_rate_limit_per_minute, 60)


def require_admin(user: User = Depends(rate_limit)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user
