"""Test settings: isolated data directory and fast password hashing."""

import os
import tempfile

os.environ["FABRIC_DATA_DIR"] = os.environ.get("FABRIC_TEST_DATA_DIR") or tempfile.mkdtemp(
    prefix="homefabric-test-")
os.environ.setdefault("FABRIC_SECRET_KEY", "test-secret-key-not-for-production-use-0123456789")
os.environ["FABRIC_ENABLE_LOG_CHANNEL"] = "1"
os.environ["FABRIC_PUBLIC_BASE_URL"] = "https://home.example.test"
os.environ["FABRIC_ALLOWED_HOSTS"] = "testserver,localhost"

from homefabric.settings import *  # noqa: E402,F403

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher",
                    "django.contrib.auth.hashers.PBKDF2PasswordHasher"]
MAINTENANCE_DRAIN_SECONDS = 0
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
EMAIL_CONFIGURED = True
SECURE_COOKIES = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
LOGGING["root"]["level"] = "WARNING"  # noqa: F405
LOGGING["loggers"]["django"]["level"] = "ERROR"  # noqa: F405
LOGGING["loggers"]["django.request"] = {"level": "CRITICAL"}  # noqa: F405
