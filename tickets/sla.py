from datetime import timedelta

from django.utils import timezone


def calculate_sla_deadline(priority: str) -> "datetime | None":
    """
    Calcula el deadline SLA para un ticket según su prioridad.
    Devuelve None si no existe política SLA para esa prioridad.
    Importación tardía de SLAPolicy para evitar circular imports.
    """
    from .models import SLAPolicy

    try:
        policy = SLAPolicy.objects.get(priority=priority)
    except SLAPolicy.DoesNotExist:
        return None

    return timezone.now() + timedelta(hours=policy.resolution_hours)


def evaluate_sla_met(sla_deadline, resolved_at) -> "bool | None":
    """
    Determina si un ticket cumplió su SLA.
    - True:  resuelto antes del deadline
    - False: resuelto después del deadline
    - None:  información insuficiente para evaluar
    """
    if not sla_deadline or not resolved_at:
        return None
    return resolved_at <= sla_deadline


def format_minutes(minutes: int) -> str:
    """Convierte minutos a una cadena legible (Xh Ym o Xm)."""
    if minutes is None:
        return "—"
    minutes = max(0, int(minutes))
    if minutes < 60:
        return f"{minutes}m"
    h, m = divmod(minutes, 60)
    return f"{h}h {m}m" if m else f"{h}h"
