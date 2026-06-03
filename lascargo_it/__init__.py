# Carga el app de Celery al iniciar Django para que @shared_task funcione.
# Si Celery no está instalado, el import falla silenciosamente — no afecta el resto.
try:
    from .celery import app as celery_app  # noqa: F401
except ImportError:
    pass
