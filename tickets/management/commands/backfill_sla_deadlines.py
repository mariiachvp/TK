"""
Management command: backfill_sla_deadlines

Rellena sla_deadline en tickets que no lo tienen (creados antes de que
existiera la lógica de SLA). Usa created_at + policy.resolution_hours
como referencia para respetar la fecha original de creación.

Uso:
    python manage.py backfill_sla_deadlines
    python manage.py backfill_sla_deadlines --dry-run
"""

from datetime import timedelta

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Rellena sla_deadline en tickets sin deadline usando created_at como referencia"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra los tickets que serían actualizados sin modificarlos",
        )

    def handle(self, *args, **options):
        from tickets.models import SLAPolicy, Ticket

        dry_run    = options["dry_run"]
        candidates = Ticket.objects.filter(sla_deadline__isnull=True).order_by("created_at")

        if not candidates.exists():
            self.stdout.write(self.style.SUCCESS("No hay tickets sin sla_deadline. Nada que hacer."))
            return

        # Pre-cargar políticas
        policies = {p.priority: p for p in SLAPolicy.objects.all()}

        updated = 0
        skipped = 0

        for ticket in candidates:
            policy = policies.get(ticket.priority)
            if not policy:
                self.stdout.write(
                    self.style.WARNING(
                        f"  #{ticket.id} [{ticket.priority}] — sin política SLA, omitido"
                    )
                )
                skipped += 1
                continue

            deadline = ticket.created_at + timedelta(hours=policy.resolution_hours)
            self.stdout.write(
                f"  #{ticket.id} [{ticket.priority}] creado {ticket.created_at:%d/%m/%Y %H:%M}"
                f" -> deadline {deadline:%d/%m/%Y %H:%M}"
                + (" (dry-run)" if dry_run else "")
            )

            if not dry_run:
                # .update() directo para no disparar signals
                Ticket.objects.filter(pk=ticket.pk).update(sla_deadline=deadline)
                updated += 1

        if dry_run:
            self.stdout.write(self.style.WARNING("\n[DRY RUN] Nada fue modificado."))
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nActualizados: {updated} | Omitidos (sin política): {skipped}"
            ))
