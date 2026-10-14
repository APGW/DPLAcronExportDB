from __future__ import annotations

import os
import threading
import unittest
from unittest.mock import patch

from dpl_export.auth import (
    AuthConfig,
    AuthenticationError,
    AuthenticationRateLimitError,
    MAX_FAILED_ATTEMPTS,
    SessionStore,
    auth_from_environment,
    hash_password,
    verify_password,
)


class AuthenticationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.password = "test-password-123!"
        self.password_hash = hash_password(self.password, iterations=100_000)

    def test_password_is_salted_and_verified(self) -> None:
        second_hash = hash_password(self.password, iterations=100_000)
        self.assertNotEqual(self.password_hash, second_hash)
        self.assertTrue(verify_password(self.password, self.password_hash))
        self.assertFalse(verify_password("wrong-password", self.password_hash))
        self.assertNotIn(self.password, self.password_hash)

    def test_environment_requires_a_valid_hash(self) -> None:
        with patch.dict(os.environ, {
            "DPL_AUTH_USERNAME": "admin",
            "DPL_AUTH_PASSWORD_HASH": self.password_hash,
            "DPL_SECURE_COOKIE": "1",
            "DPL_TRUST_PROXY": "1",
        }, clear=True):
            config = auth_from_environment()
        self.assertEqual("admin", config.username)
        self.assertTrue(config.secure_cookie)
        self.assertTrue(config.trusted_proxy)

        with patch.dict(os.environ, {
            "DPL_AUTH_USERNAME": "admin",
            "DPL_AUTH_PASSWORD_HASH": "plaintext-is-not-valid",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "PASSWORD_HASH"):
                auth_from_environment()

    def test_sessions_store_only_a_token_digest_and_expire_on_logout(self) -> None:
        store = SessionStore(AuthConfig("admin", self.password_hash))
        token, session = store.login("admin", self.password, "127.0.0.1")
        self.assertNotIn(token, store._sessions)
        self.assertIs(store.get(token), session)
        store.logout(token)
        self.assertIsNone(store.get(token))

    def test_repeated_failed_logins_are_limited(self) -> None:
        store = SessionStore(AuthConfig("admin", self.password_hash))
        for _ in range(MAX_FAILED_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                store.login("admin", "wrong-password", "127.0.0.1")
        with self.assertRaises(AuthenticationRateLimitError):
            store.login("admin", self.password, "127.0.0.1")

    def test_concurrent_password_checks_are_bounded(self) -> None:
        store = SessionStore(AuthConfig("admin", self.password_hash))
        release = threading.Event()
        two_started = threading.Event()
        counter_lock = threading.Lock()
        started = 0
        failures = []

        def slow_verify(_password, _encoded):
            nonlocal started
            with counter_lock:
                started += 1
                if started == 2:
                    two_started.set()
            release.wait(timeout=5)
            return False

        def attempt(client):
            try:
                store.login("admin", "wrong-password", client)
            except AuthenticationError as exc:
                failures.append(exc)

        with patch("dpl_export.auth.verify_password", side_effect=slow_verify):
            threads = [threading.Thread(target=attempt, args=(f"client-{index}",)) for index in range(2)]
            for thread in threads:
                thread.start()
            self.assertTrue(two_started.wait(timeout=5))
            with self.assertRaises(AuthenticationRateLimitError):
                store.login("admin", "wrong-password", "client-3")
            release.set()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(2, started)
        self.assertEqual(2, len(failures))


if __name__ == "__main__":
    unittest.main()
