"""
Sistema de notificaciones por correo vía Microsoft Graph API.

Arquitectura:
  - Cada notificación corre en un daemon thread (no bloquea el request).
  - Si las credenciales de Azure no están configuradas, las funciones retornan
    silenciosamente sin error — el sistema funciona igual sin notificaciones.
  - Cuando Celery está disponible (CELERY_BROKER_URL configurado), el envío
    se delega a tasks.send_notification_email con 3 reintentos automáticos.

Eventos notificados:
  - Ticket creado          → equipo IT + confirmación al solicitante
  - Ticket asignado        → técnico asignado + aviso al solicitante
  - Estado cambia          → solicitante (cuando IT cambia) / IT+técnico (cuando usuario reabre)
  - Comentario público     → la otra parte (IT→usuario o usuario→IT+técnico)
  - SLA próximo a vencer   → técnico asignado + equipo IT (vía management command)
"""

import logging
import threading

from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.template.loader import render_to_string

from .models import SystemAuditLog, Ticket

logger = logging.getLogger("tickets.notifications")


# ---------------------------------------------------------------------------
# Verificación de configuración
# ---------------------------------------------------------------------------

def _is_enabled() -> bool:
    """Devuelve True solo si todas las credenciales de Azure están configuradas."""
    return bool(
        getattr(settings, "AZURE_TENANT_ID", "")
        and getattr(settings, "AZURE_CLIENT_ID", "")
        and getattr(settings, "AZURE_CLIENT_SECRET", "")
        and getattr(settings, "GRAPH_SENDER_EMAIL", "")
    )


# ---------------------------------------------------------------------------
# Envío asíncrono (background thread o Celery)
# ---------------------------------------------------------------------------

def _send_async(to_email: str, subject: str, template: str, context: dict) -> None:
    """
    Renderiza el template HTML y despacha el envío de forma asíncrona.

    - Si CELERY_BROKER_URL está configurado → tarea Celery (con reintentos).
    - Si no                                 → daemon thread (sin reintentos).

    Los errores se capturan y loguean — nunca propagan al caller.
    """
    if not to_email:
        logger.debug("Notificación omitida: destinatario vacío — %s", subject)
        return

    try:
        body = render_to_string(template, context)
    except Exception as exc:
        logger.error("Error al renderizar template %s: %s", template, exc)
        return

    if getattr(settings, "CELERY_BROKER_URL", ""):
        try:
            from .tasks import send_notification_email
            send_notification_email.delay(to_email, subject, body)
            return
        except Exception as exc:
            logger.warning("Celery no disponible, usando thread: %s", exc)

    def _do_send():
        try:
            from .graph import send_mail
            send_mail(to_email, subject, body)
        except Exception as exc:
            logger.error("Fallo al enviar notificación a %s [%s]: %s", to_email, subject, exc)

    threading.Thread(target=_do_send, daemon=True).start()


def _ticket_ref(ticket) -> str:
    """Retorna el identificador del ticket para usar en asuntos de correo."""
    return ticket.ticket_number or f"#{ticket.id}"


def _log_notification(ticket: "Ticket", event_type: str, recipient: str) -> None:
    """Registra en SystemAuditLog que se despachó una notificación."""
    try:
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.NOTIFICATION_SENT,
            details=f"[{_ticket_ref(ticket)}] {event_type} → {recipient}",
        )
    except Exception as exc:
        logger.warning("No se pudo registrar notificación en auditoría: %s", exc)


def _ctx(ticket, **extra) -> dict:
    return {
        "ticket":  ticket,
        "app_url": getattr(settings, "APP_BASE_URL", "").rstrip("/"),
        **extra,
    }


# ---------------------------------------------------------------------------
# Funciones de notificación públicas
# ---------------------------------------------------------------------------

def notify_new_ticket(ticket: Ticket) -> None:
    """Notifica al equipo IT que llegó un nuevo ticket."""
    if not _is_enabled():
        return
    it_email = getattr(settings, "IT_TEAM_EMAIL", "")
    if not it_email:
        logger.warning("IT_TEAM_EMAIL no configurado — notificación de nuevo ticket omitida")
        return

    _send_async(
        to_email=it_email,
        subject=f"[Nuevo Ticket {_ticket_ref(ticket)}] {ticket.title}",
        template="tickets/emails/new_ticket.html",
        context=_ctx(ticket),
    )
    _log_notification(ticket, "nuevo_ticket_IT", it_email)
    logger.info("Notificación 'nuevo ticket' despachada para ticket %s", _ticket_ref(ticket))


def notify_new_ticket_confirmation(ticket: Ticket) -> None:
    """Confirma al solicitante que su ticket fue recibido correctamente."""
    if not _is_enabled():
        return
    email = ticket.requester.email
    if not email:
        logger.warning(
            "Solicitante '%s' sin email — confirmación de nuevo ticket omitida",
            ticket.requester.username,
        )
        return

    _send_async(
        to_email=email,
        subject=f"[Confirmación] Tu ticket {_ticket_ref(ticket)} fue registrado",
        template="tickets/emails/new_ticket_confirmation.html",
        context=_ctx(ticket),
    )
    _log_notification(ticket, "confirmacion_creacion", email)
    logger.info("Confirmación de creación despachada para ticket %s → %s", _ticket_ref(ticket), email)


def notify_ticket_assigned(ticket: Ticket) -> None:
    """Notifica al técnico recién asignado."""
    if not _is_enabled():
        return
    if not ticket.assigned_to:
        return
    email = ticket.assigned_to.email
    if not email:
        logger.warning(
            "Técnico '%s' sin email — notificación de asignación al técnico omitida",
            ticket.assigned_to.username,
        )
        return

    _send_async(
        to_email=email,
        subject=f"[Asignado] {_ticket_ref(ticket)}: {ticket.title}",
        template="tickets/emails/ticket_assigned.html",
        context=_ctx(ticket),
    )
    _log_notification(ticket, "asignado_tecnico", email)
    logger.info("Notificación 'asignado al técnico' despachada para ticket %s → %s", _ticket_ref(ticket), email)


def notify_ticket_assigned_user(ticket: Ticket) -> None:
    """Informa al solicitante quién es el técnico responsable de su ticket."""
    if not _is_enabled():
        return
    if not ticket.assigned_to:
        return
    email = ticket.requester.email
    if not email:
        logger.warning(
            "Solicitante '%s' sin email — notificación de asignación al usuario omitida",
            ticket.requester.username,
        )
        return

    tech_name = ticket.assigned_to.get_full_name() or ticket.assigned_to.username
    _send_async(
        to_email=email,
        subject=f"[{_ticket_ref(ticket)}] Tu ticket fue asignado a {tech_name}",
        template="tickets/emails/ticket_assigned_user.html",
        context=_ctx(ticket),
    )
    _log_notification(ticket, "asignado_usuario_informado", email)
    logger.info(
        "Notificación 'asignado al usuario' despachada para ticket %s → %s",
        _ticket_ref(ticket), email,
    )


def notify_status_change(ticket: Ticket, old_status: str) -> None:
    """Notifica al solicitante que el estado de su ticket cambió (acción de IT)."""
    if not _is_enabled():
        return
    email = ticket.requester.email
    if not email:
        logger.warning(
            "Solicitante '%s' sin email — notificación de cambio de estado omitida",
            ticket.requester.username,
        )
        return

    _send_async(
        to_email=email,
        subject=f"[{_ticket_ref(ticket)}] Estado actualizado: {ticket.get_status_display()}",
        template="tickets/emails/status_changed.html",
        context=_ctx(ticket, old_status=old_status),
    )
    _log_notification(ticket, f"estado_cambiado:{old_status}→{ticket.status}", email)
    logger.info(
        "Notificación 'estado' despachada para ticket %s → %s (%s → %s)",
        _ticket_ref(ticket), email, old_status, ticket.status,
    )


def notify_comment_added(ticket: Ticket, author, content: str, is_public: bool) -> None:
    """
    Notifica a la otra parte cuando se agrega un comentario público.

    - IT comenta → avisa al solicitante.
    - Usuario comenta → avisa al equipo IT y al técnico asignado.
    - Notas internas (is_public=False) no generan ninguna notificación.
    """
    if not _is_enabled():
        return
    if not is_public:
        return

    profile = getattr(author, "profile", None)
    author_is_it = bool(profile and profile.is_it_staff)
    ctx = _ctx(ticket, comment_content=content, comment_author=author, author_is_it=author_is_it)

    if author_is_it:
        email = ticket.requester.email
        if not email:
            return
        _send_async(
            to_email=email,
            subject=f"[{_ticket_ref(ticket)}] Nueva respuesta de IT",
            template="tickets/emails/comment_added.html",
            context=ctx,
        )
        _log_notification(ticket, "comentario_IT→usuario", email)
        logger.info("Notificación 'comentario IT→usuario' despachada para ticket %s", _ticket_ref(ticket))
    else:
        it_email       = getattr(settings, "IT_TEAM_EMAIL", "")
        assignee_email = ticket.assigned_to.email if (ticket.assigned_to and ticket.assigned_to.email) else ""
        recipients     = {e for e in [it_email, assignee_email] if e}
        for email in recipients:
            _send_async(
                to_email=email,
                subject=f"[{_ticket_ref(ticket)}] Nuevo comentario del usuario",
                template="tickets/emails/comment_added.html",
                context=ctx,
            )
            _log_notification(ticket, "comentario_usuario→IT", email)
        if recipients:
            logger.info(
                "Notificación 'comentario usuario→IT' despachada para ticket %s → %s",
                _ticket_ref(ticket), ", ".join(recipients),
            )


def notify_ticket_reopened(ticket: Ticket) -> None:
    """Notifica al equipo IT y al técnico asignado cuando el usuario reabre un ticket."""
    if not _is_enabled():
        return
    it_email       = getattr(settings, "IT_TEAM_EMAIL", "")
    assignee_email = ticket.assigned_to.email if (ticket.assigned_to and ticket.assigned_to.email) else ""
    recipients     = {e for e in [it_email, assignee_email] if e}

    if not recipients:
        logger.warning("Sin destinatarios para notificación de reapertura de ticket %s", _ticket_ref(ticket))
        return

    for email in recipients:
        _send_async(
            to_email=email,
            subject=f"[{_ticket_ref(ticket)}] Ticket reabierto por el usuario",
            template="tickets/emails/ticket_reopened.html",
            context=_ctx(ticket),
        )
        _log_notification(ticket, "ticket_reabierto", email)
    logger.info(
        "Notificación 'reabierto' despachada para ticket %s → %s",
        _ticket_ref(ticket), ", ".join(recipients),
    )


def notify_sla_warning(ticket: Ticket) -> None:
    """
    Envía alerta de SLA próximo a vencer al técnico y al equipo IT.
    Llamado por el management command check_sla_warnings.
    """
    if not _is_enabled():
        return
    it_email       = getattr(settings, "IT_TEAM_EMAIL", "")
    assignee_email = ticket.assigned_to.email if (ticket.assigned_to and ticket.assigned_to.email) else ""
    recipients     = {e for e in [it_email, assignee_email] if e}

    if not recipients:
        logger.warning("No hay destinatarios para alerta SLA del ticket %s", _ticket_ref(ticket))
        return

    for email in recipients:
        _send_async(
            to_email=email,
            subject=f"[ALERTA SLA] {_ticket_ref(ticket)} próximo a vencer — {ticket.get_priority_display()}",
            template="tickets/emails/sla_warning.html",
            context=_ctx(ticket),
        )
        _log_notification(ticket, "alerta_SLA", email)
    logger.info(
        "Alerta SLA despachada para ticket %s → %s",
        _ticket_ref(ticket), ", ".join(recipients),
    )


# ---------------------------------------------------------------------------
# Signal receiver — dispara notificaciones automáticamente en cada save
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Ticket)
def trigger_ticket_notifications(sender, instance, created, **kwargs):
    """
    Analiza qué cambió en el ticket y dispara la notificación correspondiente.
    Usa _pre_save_state capturado por signals.py para comparar valores.
    No hace nada si las notificaciones no están habilitadas.
    """
    if not _is_enabled():
        return

    old = getattr(instance, "_pre_save_state", None)

    if created:
        notify_new_ticket(instance)              # IT team
        notify_new_ticket_confirmation(instance) # solicitante
        if instance.assigned_to_id:
            notify_ticket_assigned(instance)      # técnico
            notify_ticket_assigned_user(instance) # solicitante informado de su técnico
        return

    if old is None:
        return

    # Técnico asignado cambió
    if old.assigned_to_id != instance.assigned_to_id and instance.assigned_to_id:
        notify_ticket_assigned(instance)      # técnico nuevo
        notify_ticket_assigned_user(instance) # solicitante informado

    # Estado cambió
    if old.status != instance.status:
        if instance.status == Ticket.Status.REABIERTO:
            # Reabierto por el usuario → notificar al equipo IT / técnico
            notify_ticket_reopened(instance)
        else:
            # IT cambió el estado → notificar al solicitante
            notify_status_change(instance, old.status)
