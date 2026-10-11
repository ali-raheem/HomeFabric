"""Django settings. All instance-specific values come from homefabric.config."""

from pathlib import Path

from homefabric.config import Config, data_dir, load_or_create_secret_key

BASE_DIR = Path(__file__).resolve().parent
CONFIG = Config()

DATA_DIR = data_dir(CONFIG)
INSTANCE_DIR = DATA_DIR / "instance"
BLOB_DIR = INSTANCE_DIR / "blobs"
STAGING_DIR = DATA_DIR / "staging"
for _d in (INSTANCE_DIR, BLOB_DIR, STAGING_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SECRET_KEY = load_or_create_secret_key(CONFIG, DATA_DIR)
DEBUG = CONFIG.bool("DEBUG", False)
ALLOWED_HOSTS = CONFIG.list("ALLOWED_HOSTS", ["localhost", "127.0.0.1", "[::1]"])
PUBLIC_BASE_URL = (CONFIG.str("PUBLIC_BASE_URL", "http://localhost:8000") or "").rstrip("/")
CSRF_TRUSTED_ORIGINS = CONFIG.list("CSRF_TRUSTED_ORIGINS", [PUBLIC_BASE_URL])
BIND_ADDRESS = CONFIG.str("BIND", "127.0.0.1:8000")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.forms",
    "homefabric.core",
    "homefabric.accounts",
    "homefabric.assets",
    "homefabric.documents",
    "homefabric.maintenance",
    "homefabric.notifications",
    "homefabric.operations",
]

MIDDLEWARE = [
    "homefabric.core.middleware.RequestIdMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "homefabric.core.middleware.SecurityHeadersMiddleware",
    "homefabric.operations.middleware.MaintenanceLockMiddleware",
    "homefabric.accounts.middleware.HouseholdMiddleware",
]

ROOT_URLCONF = "homefabric.urls"
FORM_RENDERER = "homefabric.core.forms_renderer.FabricFormRenderer"
WSGI_APPLICATION = "homefabric.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "homefabric.accounts.context_processors.household",
            ],
        },
    },
]

SQLITE_BUSY_TIMEOUT_SECONDS = CONFIG.int("SQLITE_BUSY_TIMEOUT", 20)
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": INSTANCE_DIR / "db.sqlite3",
        "OPTIONS": {
            # IMMEDIATE serialises writers at BEGIN so read-check-write
            # validations (relationship cycles, unique occurrences) cannot race.
            "transaction_mode": "IMMEDIATE",
            "timeout": SQLITE_BUSY_TIMEOUT_SECONDS,
            "init_command": (
                "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL; PRAGMA foreign_keys=ON;"
            ),
        },
        "TEST": {"NAME": str(DATA_DIR / "test-db.sqlite3")},
    }
}

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = ["homefabric.accounts.backends.ThrottledModelBackend"]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "core:dashboard"
LOGOUT_REDIRECT_URL = "accounts:login"
PASSWORD_RESET_TIMEOUT = 60 * 60 * 24

LANGUAGE_CODE = CONFIG.str("LANGUAGE", "en-gb")
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
DEFAULT_HOUSEHOLD_TIMEZONE = CONFIG.str("DEFAULT_TIMEZONE", "Europe/London")
DEFAULT_HOUSEHOLD_CURRENCY = CONFIG.str("DEFAULT_CURRENCY", "GBP")

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = DATA_DIR / "static-collected"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Sessions and cookies
SESSION_COOKIE_AGE = 60 * 60 * 24 * 14
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
_secure = CONFIG.bool("SECURE_COOKIES", PUBLIC_BASE_URL.startswith("https://"))
SESSION_COOKIE_SECURE = _secure
CSRF_COOKIE_SECURE = _secure
if CONFIG.bool("BEHIND_HTTPS_PROXY", False):
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"
if _secure:
    SECURE_HSTS_SECONDS = CONFIG.int("HSTS_SECONDS", 0)

# Uploads
MAX_UPLOAD_BYTES = CONFIG.int("MAX_UPLOAD_MB", 50) * 1024 * 1024
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.TemporaryFileUploadHandler"]
FILE_UPLOAD_TEMP_DIR = str(STAGING_DIR)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o600

# Scheduling and notifications
SCHEDULE_HORIZON_DAYS = CONFIG.int("SCHEDULE_HORIZON_DAYS", 90)
NOTIFY_MAX_ATTEMPTS = CONFIG.int("NOTIFY_MAX_ATTEMPTS", 5)
NOTIFY_LEASE_SECONDS = CONFIG.int("NOTIFY_LEASE_SECONDS", 300)
NOTIFY_BACKOFF_BASE_SECONDS = CONFIG.int("NOTIFY_BACKOFF_BASE_SECONDS", 60)
NOTIFY_BACKOFF_MAX_SECONDS = CONFIG.int("NOTIFY_BACKOFF_MAX_SECONDS", 3600)
NOTIFY_CATCHUP_GRACE_SECONDS = CONFIG.int("NOTIFY_CATCHUP_GRACE_SECONDS", 15 * 60)
SCHEDULER_STALE_SECONDS = CONFIG.int("SCHEDULER_STALE_SECONDS", 10 * 60)
NOTIFICATION_ADAPTERS = {
    "email": "homefabric.notifications.adapters.EmailAdapter",
    "apprise": "homefabric.notifications.adapters.AppriseAdapter",
}
if CONFIG.bool("ENABLE_LOG_CHANNEL", DEBUG):
    NOTIFICATION_ADAPTERS["log"] = "homefabric.notifications.adapters.LogAdapter"

EMAIL_HOST = CONFIG.str("EMAIL_HOST", "")
EMAIL_PORT = CONFIG.int("EMAIL_PORT", 587)
EMAIL_HOST_USER = CONFIG.str("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = CONFIG.str("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = CONFIG.bool("EMAIL_USE_TLS", True)
EMAIL_USE_SSL = CONFIG.bool("EMAIL_USE_SSL", False)
EMAIL_TIMEOUT = CONFIG.int("EMAIL_TIMEOUT", 30)
DEFAULT_FROM_EMAIL = CONFIG.str("EMAIL_FROM", "HomeFabric <homefabric@localhost>")
EMAIL_BACKEND = (
    "django.core.mail.backends.smtp.EmailBackend"
    if EMAIL_HOST
    else "django.core.mail.backends.console.EmailBackend"
)
EMAIL_CONFIGURED = bool(EMAIL_HOST)

# Login throttling
LOGIN_FAILURE_LIMIT = CONFIG.int("LOGIN_FAILURE_LIMIT", 5)
LOGIN_FAILURE_WINDOW_SECONDS = CONFIG.int("LOGIN_FAILURE_WINDOW", 15 * 60)

# Logging
LOG_FORMAT = CONFIG.str("LOG_FORMAT", "text")
LOG_LEVEL = CONFIG.str("LOG_LEVEL", "INFO")
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "redact": {"()": "homefabric.core.logging.RedactingFilter"},
        "request_id": {"()": "homefabric.core.logging.RequestIdFilter"},
    },
    "formatters": {
        "text": {"format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s"},
        "json": {"()": "homefabric.core.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if LOG_FORMAT == "json" else "text",
            "filters": ["redact", "request_id"],
        },
    },
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
        "waitress.queue": {"level": "WARNING"},
    },
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Serve packaged static files directly from the app directories so a
# collectstatic step is optional for native installs.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = DEBUG
MAINTENANCE_DRAIN_SECONDS = float(CONFIG.str("MAINTENANCE_DRAIN_SECONDS", "2"))
