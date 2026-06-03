import os
from .base import *

# Producción — todas las variables son obligatorias, fallan explícitamente si faltan
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY no está definida. El servidor no puede iniciar.")

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME":     os.environ["DB_NAME"],
        "USER":     os.environ["DB_USER"],
        "PASSWORD": os.environ["DB_PASSWORD"],
        "HOST":     os.environ.get("DB_HOST", "localhost"),
        "PORT":     os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 600,
        "OPTIONS": {
            "connect_timeout": 10,
        },
    }
}

# Headers de seguridad HTTP
SESSION_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
X_FRAME_OPTIONS = "DENY"

# Logs rotativos en archivo para producción
# ---------------------------------------------------------------------------
# Cache — Redis obligatorio en producción para rate limiting multi-proceso.
# Configurar REDIS_URL, ej: redis://localhost:6379/0
# ---------------------------------------------------------------------------
_REDIS_URL = os.environ.get("REDIS_URL", "")
if not _REDIS_URL:
    raise RuntimeError(
        "REDIS_URL no está definida. El rate limiting de login no funcionará "
        "correctamente con múltiples workers. Configura Redis y define REDIS_URL."
    )
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": _REDIS_URL,
    }
}

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOGGING["handlers"]["file"] = {
    "class": "logging.handlers.RotatingFileHandler",
    "filename": LOGS_DIR / "app.log",
    "maxBytes": 1024 * 1024 * 5,   # 5 MB por archivo
    "backupCount": 5,
    "formatter": "verbose",
}
LOGGING["root"]["handlers"] = ["console", "file"]
LOGGING["loggers"]["tickets"]["handlers"] = ["console", "file"]
LOGGING["loggers"]["django"]["handlers"] = ["console", "file"]
