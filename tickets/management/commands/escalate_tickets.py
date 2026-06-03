"""
Management command: escalate_tickets

Detecta tickets activos (PENDIENTE / EN_PROCESO) que llevan demasiado tiempo
sin actualizarse y los sube un nivel de prioridad automáticamente:
    LOW -> MEDIUM -> HIGH -> CRITICAL

Para CRITICAL (ya no escalable) envía una alerta SLA al equipo IT.

Cada escalación queda registrada en el AuditLog del ticket.
Los umbrales de tiempo se configuran en settings.ESCALATION_THRESHOLDS.

Uso:
    python manage.py escalate_tickets
    python manage.py escalate_tickets --dry-run

Programar en Windows Task Scheduler (cada 30 min):
    Programa:   C:\\ruta\\venv\\Scripts\\python.exe
    Argumentos: manage.py escalate_tickets
    Inicio en:  C:\\ruta\\lascargo_it
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


_PRIORITY_NEXT = {
    "LOW":    "MEDIUM",
    "MEDIUM": "HIGH",
    "HIGH":   "CRITICAL",
}

_DEFAULT_THRESHOLDS = {
    "LOW":      72,  # horas sin actividad antes de escalar a MEDIUM
    "MEDIUM":   48,  # horas antes de escalar a HIGH
    "HIGH":     12,  # horas antes de escalar a CRITICAL
    "CRITICAL":  4,  # horas antes de enviar alerta (ya no puede escalar más)
}


class Command(BaseCommand):
    help = "Escala automáticamente tickets inactivos según umbrales de prioridad"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista los tickets que serían escalados sin modificarlos",
        )

    def handle(self, *args, **options):
        from tickets.models import AuditLog, Ticket
        from tickets.notifications import notify_sla_warning

        dry_run    = options["dry_run"]
        now        = timezone.now()
        thresholds = getattr(settings, "ESCALATION_THRESHOLDS", _DEFAULT_THRESHOLDS)

        active_statuses = [Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO]
        total_escalated = 0
        total_alerted   = 0

        for priority, hours in thresholds.items():
            cutoff = now - timedelta(hours=hours)

            candidates = (
                Ticket.objects
                .filter(
                    priority=priority,
                    status__in=active_statuses,
                    updated_at__lt=cutoff,
                )
                .select_related("assigned_to", "requester")
                .order_by("updated_at")
            )

            if not candidates.exists():
                continue

            next_priority = _PRIORITY_NEXT.get(priority)

            for ticket in candidates:
                idle_hours = int((now - ticket.updated_at).total_seconds() / 3600)
                label = ticket.get_priority_display()
                next_label = dict(Ticket.Priority.choices).get(next_priority, "—") if next_priority else "—"

                if next_priority:
                    # Escalar al siguiente nivel
                    self.stdout.write(
                        f"  #{ticket.id} [{label}] inactivo {idle_hours}h -> "
                        + (f"[{next_label}]" if not dry_run else f"[{next_label}] (dry-run)")
                        + f" — {ticket.title[:55]}"
                    )
                    if not dry_run:
                        old_priority    = ticket.priority
                        ticket.priority = next_priority
                        ticket._escalation = True   # suprime FIELD_CHANGE en signal
                        ticket.save()
                        AuditLog.objects.create(
                            ticket    = ticket,
                            user      = None,
                            action    = AuditLog.Action.ESCALATED,
                            field     = "priority",
                            old_value = old_priority,
                            new_value = next_priority,
                        )
                        total_escalated += 1
                else:
                    # CRITICAL: no puede escalar más, solo alertar
                    self.stdout.write(
                        self.style.WARNING(
                            f"  #{ticket.id} [CRÍTICA] inactivo {idle_hours}h — ALERTA enviada"
                            + (" (dry-run)" if dry_run else "")
                            + f" — {ticket.title[:55]}"
                        )
                    )
                    if not dry_run:
                        notify_sla_warning(ticket)
                        total_alerted += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] Nada fue modificado."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nEscalaciones realizadas: {total_escalated} | Alertas enviadas: {total_alerted}"
            ))
