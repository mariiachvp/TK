"""
Configuración de Celery para LAS Cargo IT Service Desk.

Activación:
1. pip install celery redis
2. Configurar CELERY_BROKER_URL en settings (ej. "redis://localhost:6379/0")
3. Iniciar worker:
   - Linux/WSL:   celery -A lascargo_it worker -l info
   - Windows:     celery -A lascargo_it worker -l info -P solo
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lascargo_it.settings.local")

app = Celery("lascargo_it")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
