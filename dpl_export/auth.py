from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from dataclasses import dataclass


PASSWORD_SCHEME = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 600_000
SESSION_COOKIE_NAME = "dpl_session"
SESSION_IDLE_SECONDS = 8 * 60 * 60
SESSION_MAX_SECONDS = 12 * 60 * 60
MAX_SESSIONS = 256
MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPT_WINDOW_SECONDS = 15 * 60
MAX_CONCURRENT_PASSWORD_CHECKS = 2


class AuthenticationError(ValueError):
    pass


class AuthenticationRateLimitError(AuthenticationError):
    pass


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password_hash: str
    secure_cookie: bool = False
    trusted_proxy: bool = False


@dataclass
class Session:
    username: str
    csrf_token: str
    created_at: float
    expires_at: float


def hash_password(password: str, *, iterations: int = PASSWORD_ITERATIONS) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Das Administratorkennwort muss zwischen 12 und 256 Zeichen lang sein.")
    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    encoded_digest = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"{PASSWORD_SCHEME}${iterations}${encoded_salt}${encoded_digest}"


def _decode_base64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.b64decode(value + padding, altchars=b"-_", validate=True)


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        iterations = int(raw_iterations)
        salt = _decode_base64(raw_salt)
        expected = _decode_base64(raw_digest)
    except (ValueError, TypeError):
        return False
    if scheme != PASSWORD_SCHEME or not 100_000 <= iterations <= 2_000_000:
        return False
    if not 16 <= len(salt) <= 64 or len(expected) != 32 or len(password) > 256:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def auth_from_environment() -> AuthConfig:
    username = os.environ.get("DPL_AUTH_USERNAME", "").strip()
    password_hash = os.environ.get("DPL_AUTH_PASSWORD_HASH", "").strip()
    if not username or len(username) > 64 or any(character.isspace() for character in username):
        raise ValueError("DPL_AUTH_USERNAME fehlt oder ist ungültig.")
    if not password_hash or not verify_password("", password_hash):
        # verify_password validates the encoded structure even though an empty password never matches.
        try:
            scheme, raw_iterations, raw_salt, raw_digest = password_hash.split("$", 3)
            valid_structure = (
                scheme == PASSWORD_SCHEME
                and 100_000 <= int(raw_iterations) <= 2_000_000
                and 16 <= len(_decode_base64(raw_salt)) <= 64
                and len(_decode_base64(raw_digest)) == 32
            )
        except (ValueError, TypeError):
            valid_structure = False
        if not valid_structure:
            raise ValueError("DPL_AUTH_PASSWORD_HASH fehlt oder ist ungültig.")
    secure_cookie = os.environ.get("DPL_SECURE_COOKIE", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }
    trusted_proxy = os.environ.get("DPL_TRUST_PROXY", "0").strip().casefold() in {
        "1", "true", "yes", "on",
    }
    return AuthConfig(
        username=username,
        password_hash=password_hash,
        secure_cookie=secure_cookie,
        trusted_proxy=trusted_proxy,
    )


class SessionStore:
    def __init__(self, config: AuthConfig) -> None:
        self.config = config
        self._sessions: dict[str, Session] = {}
        self._failed_attempts: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._password_checks = threading.BoundedSemaphore(MAX_CONCURRENT_PASSWORD_CHECKS)

    @staticmethod
    def _token_key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _prune(self, now: float) -> None:
        self._sessions = {
            token: session
            for token, session in self._sessions.items()
            if session.expires_at > now and session.created_at + SESSION_MAX_SECONDS > now
        }
        cutoff = now - FAILED_ATTEMPT_WINDOW_SECONDS
        self._failed_attempts = {
            key: [attempt for attempt in attempts if attempt > cutoff]
            for key, attempts in self._failed_attempts.items()
            if any(attempt > cutoff for attempt in attempts)
        }

    def login(self, username: str, password: str, client_key: str) -> tuple[str, Session]:
        if not isinstance(username, str) or not isinstance(password, str):
            raise AuthenticationError("Benutzername oder Kennwort ist falsch.")
        if len(username) > 64 or len(password) > 256:
            raise AuthenticationError("Benutzername oder Kennwort ist falsch.")
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            attempts = self._failed_attempts.get(client_key, [])
            if len(attempts) >= MAX_FAILED_ATTEMPTS:
                raise AuthenticationRateLimitError("Zu viele Anmeldeversuche. Bitte später erneut versuchen.")
            self._failed_attempts.setdefault(client_key, []).append(now)

        if not self._password_checks.acquire(blocking=False):
            with self._lock:
                attempts = self._failed_attempts.get(client_key, [])
                if now in attempts:
                    attempts.remove(now)
            raise AuthenticationRateLimitError("Anmeldung ist ausgelastet. Bitte erneut versuchen.")
        try:
            password_matches = verify_password(password, self.config.password_hash)
            username_matches = hmac.compare_digest(
                username.encode("utf-8"), self.config.username.encode("utf-8")
            )
        finally:
            self._password_checks.release()
        if not (username_matches and password_matches):
            raise AuthenticationError("Benutzername oder Kennwort ist falsch.")

        token = secrets.token_urlsafe(32)
        session = Session(
            username=self.config.username,
            csrf_token=secrets.token_urlsafe(32),
            created_at=now,
            expires_at=now + SESSION_IDLE_SECONDS,
        )
        with self._lock:
            self._failed_attempts.pop(client_key, None)
            self._prune(now)
            if len(self._sessions) >= MAX_SESSIONS:
                oldest = min(self._sessions, key=lambda item: self._sessions[item].created_at)
                self._sessions.pop(oldest, None)
            self._sessions[self._token_key(token)] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            session = self._sessions.get(self._token_key(token))
            if session is None:
                return None
            session.expires_at = min(now + SESSION_IDLE_SECONDS, session.created_at + SESSION_MAX_SECONDS)
            return session

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(self._token_key(token), None)
