from .base import *

# Desarrollo local — no requiere variables de entorno
DEBUG = True
SECRET_KEY = "django-insecure-local-only-not-for-production"
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# En desarrollo, todos los logs a consola con nivel DEBUG
LOGGING["root"]["level"] = "DEBUG"
LOGGING["loggers"]["tickets"]["level"] = "DEBUG"

# Correos de recuperación de contraseña — se imprimen en la consola del servidor
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
