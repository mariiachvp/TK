import logging

from django.conf import settings
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.core.cache import cache
from django.db import transaction
from django.db.models import F, Q
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.utils import timezone

from .models import AuditLog, Role, SystemAuditLog, Ticket, TicketCategory, TicketCounter, TicketRoutingRule, TicketWorkSession, UserProfile
from .sla import calculate_sla_deadline, evaluate_sla_met

logger = logging.getLogger("tickets")


def _get_client_ip(request) -> str | None:
    """Extrae la IP real del cliente respetando proxies (X-Forwarded-For)."""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")

# Campos simples auditados automáticamente (assigned_to se maneja por separado)
_AUDITED_SIMPLE_FIELDS = ("status", "response", "priority", "category")

_PRIORITY_LABELS = dict(Ticket.Priority.choices)
_STATUS_LABELS   = dict(Ticket.Status.choices)


def _field_human(ticket_obj, field: str) -> str:
    """Converts a raw model field value to a human-readable audit string."""
    val = str(getattr(ticket_obj, field, "") or "")
    if field == "priority":
        return _PRIORITY_LABELS.get(val, val)
    if field == "category":
        return TicketCategory.get_label(val)
    if field == "status":
        return _STATUS_LABELS.get(val, val)
    return val


# ---------------------------------------------------------------------------
# Auditoría de sesiones — login / logout / intentos fallidos
# ---------------------------------------------------------------------------

@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    SystemAuditLog.objects.create(
        action=SystemAuditLog.Action.LOGIN,
        user=user,
        target_user=user,
        ip_address=_get_client_ip(request),
    )
    logger.info("LOGIN exitoso: '%s' desde %s", user.username, _get_client_ip(request))


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user and user.is_authenticated:
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.LOGOUT,
            user=user,
            target_user=user,
            ip_address=_get_client_ip(request),
        )
        logger.info("LOGOUT: '%s'", user.username)


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request, **kwargs):
    ip  = _get_client_ip(request)
    username = credentials.get("username", "")
    SystemAuditLog.objects.create(
        action=SystemAuditLog.Action.LOGIN_FAILED,
        ip_address=ip,
        details=f"Usuario intentado: {username}",
    )
    # Incrementa el contador de rate-limiting (lo lee el middleware para bloquear)
    key      = f"login_attempts_{ip}"
    attempts = cache.get(key, 0)
    cache.set(key, attempts + 1, 300)  # ventana de 5 minutos
    logger.warning("LOGIN fallido para '%s' desde %s (intento %d)", username, ip, attempts + 1)


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        from .models import Role
        default_role = Role.objects.filter(code="OPERADOR", is_active=True).first()
        _, was_created = UserProfile.objects.get_or_create(
            user=instance,
            defaults={"role": default_role},
        )
        if was_created:
            logger.info("UserProfile creado para usuario '%s' (rol: %s)",
                        instance.username, default_role or "sin rol")


# ---------------------------------------------------------------------------
# Ticket — pre_save: captura estado previo y auto-gestiona timestamps
# ---------------------------------------------------------------------------

@receiver(pre_save, sender=Ticket)
def prepare_ticket_before_save(sender, instance, **kwargs):
    """
    1. Captura el estado anterior (para auditoría y comparación en post_save).
    2. Para tickets nuevos: calcula sla_deadline según prioridad.
    3. Para tickets existentes: auto-gestiona started_at y resolved_at
       según el cambio de estado, sin necesidad de lógica en los views.
    """
    if not instance.pk:
        # Ticket nuevo — generar número único
        instance._pre_save_state = None
        if not instance.ticket_number:
            year = timezone.now().year
            with transaction.atomic():
                TicketCounter.objects.get_or_create(year=year, defaults={"counter": 0})
                TicketCounter.objects.filter(year=year).update(counter=F("counter") + 1)
                new_count = TicketCounter.objects.values_list("counter", flat=True).get(year=year)
            instance.ticket_number = f"TK-{year}-{new_count:06d}"
        if not instance.sla_deadline:
            instance.sla_deadline = calculate_sla_deadline(instance.priority)
        # Auto-asignación por reglas de enrutamiento (solo si aún no tiene técnico)
        if not instance.assigned_to_id:
            _auto_assign(instance)
        return

    try:
        old = Ticket.objects.select_related("assigned_to").get(pk=instance.pk)
        instance._pre_save_state = old
    except Ticket.DoesNotExist:
        instance._pre_save_state = None
        return

    # Recalcular SLA y resetear advertencia si cambia la prioridad
    if old.priority != instance.priority:
        instance.sla_deadline     = calculate_sla_deadline(instance.priority)
        instance.sla_warning_sent = False  # la nueva deadline necesita su propia advertencia

    # Auto-gestionar timestamps según transición de estado
    if old.status != instance.status:
        now = timezone.now()

        if instance.status == Ticket.Status.EN_PROCESO and not instance.started_at:
            instance.started_at = now
            logger.debug("Ticket #%s: started_at = %s", instance.pk, now)

        elif instance.status in (Ticket.Status.RESUELTO, Ticket.Status.CERRADO) and not instance.resolved_at:
            instance.resolved_at = now
            logger.debug("Ticket #%s: resolved_at = %s", instance.pk, now)

        elif instance.status in (Ticket.Status.ABIERTO, Ticket.Status.REABIERTO):
            # Vuelve a estado abierto: reiniciamos tiempos para medición limpia
            instance.started_at  = None
            instance.resolved_at = None


# ---------------------------------------------------------------------------
# Ticket — post_save: auditoría, sesiones de trabajo, SLA
# ---------------------------------------------------------------------------

@receiver(post_save, sender=Ticket)
def handle_ticket_post_save(sender, instance, created, **kwargs):
    """
    1. Registra en AuditLog los campos que cambiaron.
    2. Gestiona TicketWorkSession (inicio/cierre) según estado.
    3. Evalúa sla_met al cerrar el ticket (usa .update() para evitar recursión).
    """
    changed_by = getattr(instance, "_changed_by", None)
    old        = getattr(instance, "_pre_save_state", None)

    # --- Auditoría ---
    if created:
        AuditLog.objects.create(
            ticket=instance,
            user=changed_by,
            action=AuditLog.Action.CREATED,
            new_value=instance.title,
        )
        return

    if old is None:
        return

    is_escalation = getattr(instance, "_escalation", False)

    # Audit simple fields with human-readable values
    for field in _AUDITED_SIMPLE_FIELDS:
        old_val = _field_human(old, field)
        new_val = _field_human(instance, field)
        if old_val == new_val:
            continue

        # Skip priority FIELD_CHANGE when escalate_tickets already logs ESCALATED
        if field == "priority" and is_escalation:
            continue

        if field == "status":
            action = (
                AuditLog.Action.REOPENED
                if instance.status == Ticket.Status.REABIERTO
                else AuditLog.Action.STATUS_CHANGE
            )
        elif field == "response":
            action = AuditLog.Action.RESPONDED
        else:
            action = AuditLog.Action.FIELD_CHANGE

        AuditLog.objects.create(
            ticket=instance,
            user=changed_by,
            action=action,
            field=field,
            old_value=old_val,
            new_value=new_val,
        )

    # Audit technician assignment separately (ASSIGNED vs REASSIGNED)
    old_tech_id = getattr(old, "assigned_to_id", None)
    new_tech_id = getattr(instance, "assigned_to_id", None)
    if old_tech_id != new_tech_id:
        old_user = old.assigned_to  # pre-fetched via select_related
        new_user = instance.assigned_to  # cached by form, or lazy-fetched
        old_name = (old_user.get_full_name() or old_user.username) if old_user else ""
        new_name = (new_user.get_full_name() or new_user.username) if new_user else ""
        assignment_action = AuditLog.Action.ASSIGNED if not old_tech_id else AuditLog.Action.REASSIGNED
        AuditLog.objects.create(
            ticket=instance,
            user=changed_by,
            action=assignment_action,
            field="assigned_to",
            old_value=old_name,
            new_value=new_name,
        )

    # --- Sesiones de trabajo ---
    if old.status != instance.status:
        _manage_work_session(instance, old, changed_by)

    # --- SLA: evaluar al resolver o cerrar (la primera vez que entra a ese estado) ---
    resolved_statuses = {Ticket.Status.RESUELTO, Ticket.Status.CERRADO}
    if instance.status in resolved_statuses and old.status not in resolved_statuses:
        sla_result = evaluate_sla_met(instance.sla_deadline, instance.resolved_at)
        if sla_result is not None:
            # .update() directo para evitar disparar este signal de nuevo
            Ticket.objects.filter(pk=instance.pk).update(sla_met=sla_result)
            label = "CUMPLIDO" if sla_result else "VENCIDO"
            logger.info("Ticket #%s SLA %s", instance.pk, label)


def _manage_work_session(instance: Ticket, old: Ticket, changed_by):
    """Centraliza la lógica de inicio/cierre de TicketWorkSession."""
    now = timezone.now()

    if instance.status == Ticket.Status.EN_PROCESO:
        technician = changed_by or instance.assigned_to
        if technician:
            TicketWorkSession.objects.create(
                ticket=instance,
                technician=technician,
                started_at=instance.started_at or now,
            )
            logger.debug(
                "WorkSession iniciada — Ticket #%s, técnico: %s",
                instance.pk, technician.username,
            )

    elif instance.status in (Ticket.Status.RESUELTO, Ticket.Status.CERRADO):
        ended = instance.resolved_at or now
        closed_count = TicketWorkSession.objects.filter(
            ticket=instance, ended_at__isnull=True
        ).update(ended_at=ended)
        if closed_count:
            logger.debug(
                "WorkSession(s) cerradas — Ticket #%s (%s sesión/es)",
                instance.pk, closed_count,
            )

    elif instance.status in (Ticket.Status.ABIERTO, Ticket.Status.REABIERTO):
        # Ticket reabierto o regresado a abierto: cerrar sesiones pendientes
        TicketWorkSession.objects.filter(
            ticket=instance, ended_at__isnull=True
        ).update(ended_at=now)


def _auto_assign(instance: Ticket) -> None:
    """
    Busca la regla de enrutamiento más específica (área + categoría tiene precedencia
    sobre categoría sola) y asigna el técnico correspondiente al ticket.
    """
    rule = (
        TicketRoutingRule.objects
        .filter(is_active=True)
        .filter(
            Q(category=instance.category, area=instance.area)
            | Q(category=instance.category, area="")
        )
        .select_related("assigned_to")
        .order_by("-area")  # regla con área específica primero (no vacía > vacía)
        .first()
    )
    if rule and rule.assigned_to:
        instance.assigned_to = rule.assigned_to
        logger.info(
            "Auto-asignación: ticket categoría '%s' / área '%s' → técnico '%s'",
            instance.category, instance.area, rule.assigned_to.username,
        )
