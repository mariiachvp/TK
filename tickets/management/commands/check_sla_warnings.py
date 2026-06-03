"""
Management command: check_sla_warnings

Detecta tickets activos cuyo SLA deadline vence dentro de N horas y envía
una alerta por correo al técnico asignado y al equipo IT.

Diseñado para ejecutarse periódicamente vía cron (Linux) o Task Scheduler (Windows).

Uso:
    python manage.py check_sla_warnings
    python manage.py check_sla_warnings --hours 4
    python manage.py check_sla_warnings --dry-run

Programar en Windows Task Scheduler:
    Programa:  C:\\ruta\\venv\\Scripts\\python.exe
    Argumentos: manage.py check_sla_warnings --hours 2
    Inicio en:  C:\\ruta\\lascargo_it
    Cada:       30 minutos
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Envía alertas de correo para tickets próximos a vencer su SLA"

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=2,
            help="Horas de antelación para enviar la alerta (default: 2)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra los tickets en riesgo sin enviar correos",
        )

    def handle(self, *args, **options):
        from tickets.models import Ticket
        from tickets.notifications import notify_sla_warning, _is_enabled

        hours    = options["hours"]
        dry_run  = options["dry_run"]
        now      = timezone.now()
        deadline = now + timedelta(hours=hours)

        at_risk = (
            Ticket.objects
            .filter(
                sla_deadline__isnull=False,
                sla_deadline__lte=deadline,
                sla_deadline__gt=now,
                status__in=[Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO],
                sla_warning_sent=False,
            )
            .select_related("assigned_to", "requester")
            .order_by("sla_deadline")
        )

        if not at_risk.exists():
            self.stdout.write("Sin tickets en riesgo de vencer SLA en las próximas %dh." % hours)
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[DRY RUN] Tickets que recibirían alerta SLA (próximas {hours}h):"
            ))
        elif not _is_enabled():
            self.stdout.write(self.style.WARNING(
                "Notificaciones no habilitadas (credenciales Azure ausentes). "
                "Marcaremos sla_warning_sent=True de todas formas para evitar ruido en logs. "
                "Use --dry-run para solo listar sin modificar."
            ))

        count = 0
        for ticket in at_risk:
            remaining_min = int((ticket.sla_deadline - now).total_seconds() / 60)
            remaining_str = (
                f"{remaining_min // 60}h {remaining_min % 60}m"
                if remaining_min >= 60 else f"{remaining_min}m"
            )
            assignee = (
                ticket.assigned_to.get_full_name() or ticket.assigned_to.username
                if ticket.assigned_to else "Sin asignar"
            )
            self.stdout.write(
                f"  #{ticket.id} [{ticket.get_priority_display()}] "
                f"vence en {remaining_str} — {ticket.title[:60]} — {assignee}"
            )

            if not dry_run:
                notify_sla_warning(ticket)
                Ticket.objects.filter(pk=ticket.pk).update(sla_warning_sent=True)

            count += 1

        if dry_run:
            self.stdout.write(self.style.WARNING(f"\nTotal: {count} ticket(s) en riesgo (dry-run, nada enviado)"))
        else:
            self.stdout.write(self.style.SUCCESS(f"\nAlertas despachadas: {count} ticket(s)"))
