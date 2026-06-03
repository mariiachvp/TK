"""
Tareas Celery para notificaciones por correo con reintentos automáticos.

Solo se usa cuando CELERY_BROKER_URL está configurado en settings.
Si no, notifications.py usa daemon threads como fallback.

El body HTML se pre-renderiza en el caller (antes de despachar la tarea) para
evitar pasar objetos Django (no serializables en JSON) al worker.
"""

try:
    from celery import shared_task
except ImportError:
    # Celery no instalado — este módulo no se importa en ese caso
    raise

import logging

logger = logging.getLogger("tickets.notifications")


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_notification_email(self, to_email: str, subject: str, body_html: str) -> None:
    """
    Envía un correo vía Graph API desde el worker de Celery.
    Reintenta hasta 3 veces con 60s de espera entre intentos.
    """
    try:
        from tickets.graph import send_mail
        send_mail(to_email, subject, body_html)
        logger.info("Correo enviado a %s — %s", to_email, subject)
    except Exception as exc:
        logger.warning("Intento %d fallido para %s: %s", self.request.retries + 1, to_email, exc)
        raise self.retry(exc=exc)
