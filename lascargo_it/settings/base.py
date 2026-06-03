import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Obligatorio en producción — falla explícitamente si no está definido
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
DEBUG = os.getenv("DJANGO_DEBUG", "False") == "True"
ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "").split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "tickets.apps.TicketsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tickets.middleware.LoginRateLimitMiddleware",
]

ROOT_URLCONF = "lascargo_it.urls"

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
            ],
        },
    },
]

WSGI_APPLICATION = "lascargo_it.wsgi.application"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "es-co"
TIME_ZONE = "America/Bogota"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL  = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Límite de tamaño por archivo adjunto (bytes)
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB

LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/accounts/login/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Cache — LocMem para desarrollo; producción sobreescribe con Redis.
# El rate limiting de login (LoginRateLimitMiddleware) requiere cache compartido
# entre workers. Con LocMem cada worker tiene su propio contador independiente.
# ---------------------------------------------------------------------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "lascargo-it",
    }
}

# ---------------------------------------------------------------------------
# Microsoft Graph API — Fase 3
# Dejar en blanco para deshabilitar notificaciones sin romper el sistema.
# ---------------------------------------------------------------------------
AZURE_TENANT_ID     = os.getenv("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID     = os.getenv("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.getenv("AZURE_CLIENT_SECRET", "")
GRAPH_SENDER_EMAIL  = os.getenv("GRAPH_SENDER_EMAIL", "")  # mailbox del remitente
IT_TEAM_EMAIL       = os.getenv("IT_TEAM_EMAIL", "")       # destino de nuevos tickets
APP_BASE_URL        = os.getenv("APP_BASE_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Celery — Fase 5 (opt-in: dejar vacío para usar daemon threads)
# Configurar CELERY_BROKER_URL para activar colas con reintentos.
# ---------------------------------------------------------------------------
CELERY_BROKER_URL          = os.getenv("CELERY_BROKER_URL", "")
CELERY_RESULT_BACKEND      = os.getenv("CELERY_RESULT_BACKEND", "")
CELERY_ACCEPT_CONTENT      = ["json"]
CELERY_TASK_SERIALIZER     = "json"
CELERY_RESULT_SERIALIZER   = "json"
CELERY_TIMEZONE            = "America/Bogota"

# ---------------------------------------------------------------------------
# Escalación automática — Fase 5
# Horas de inactividad (updated_at) antes de subir la prioridad del ticket.
# ---------------------------------------------------------------------------
ESCALATION_THRESHOLDS = {
    "LOW":      72,   # → MEDIUM
    "MEDIUM":   48,   # → HIGH
    "HIGH":     12,   # → CRITICAL
    "CRITICAL":  4,   # → solo alerta, no escala más
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {module} — {message}",
            "style": "{",
        },
        "simple": {
            "format": "[{levelname}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.getenv("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "tickets": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "tickets.graph": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "tickets.notifications": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
