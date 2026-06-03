# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Django-based IT Service Desk ticket management system for LAS Cargo. Non-IT staff (OMA and OPERADOR roles) submit support tickets; IT staff manages and responds to them. All UI is server-rendered Django templates — no frontend build step.

## Common Commands

```bash
# Development — settings module must be specified (no default settings.py at root level)
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py runserver
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py migrate
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py makemigrations
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py shell
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py createsuperuser

# Tests (no test suite exists yet — tests.py is empty)
DJANGO_SETTINGS_MODULE=lascargo_it.settings.local python manage.py test tickets

# Management commands (scheduled via Windows Task Scheduler or cron)
python manage.py check_sla_warnings          # Send SLA alerts for tickets expiring soon
python manage.py check_sla_warnings --hours 4 --dry-run
python manage.py escalate_tickets            # Auto-escalate stale tickets by priority
python manage.py escalate_tickets --dry-run
python manage.py backfill_sla_deadlines      # One-time fix: recalculate SLA deadlines
```

Install dependencies with `pip install -r requirements.txt`. For Celery support (optional): `pip install celery redis`.

Production runs via Gunicorn (3 workers) managed by systemd (`tickets.service`) with `DJANGO_SETTINGS_MODULE=lascargo_it.settings.production`.

## Settings Structure

Settings are split into three files under `lascargo_it/settings/`:

- `base.py` — shared config, reads all secrets from environment variables
- `local.py` — hardcoded dev values (SQLite, DEBUG=True), no env vars needed
- `production.py` — PostgreSQL, security headers, rotating file logs; crashes on startup if `DJANGO_SECRET_KEY` is missing

Environment variables are documented in `.env.example`. Notable optional ones:
- `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `GRAPH_SENDER_EMAIL`, `IT_TEAM_EMAIL` — enables email notifications via Microsoft Graph API (safe to omit)
- `CELERY_BROKER_URL` — enables async email delivery with retries (falls back to daemon threads if absent)
- `ESCALATION_THRESHOLDS` — dict of `{priority: hours_before_escalation}` in settings

## Architecture

### Role-Based Access

Three user roles drive all access control: **IT**, **OMA**, **OPERADOR** (default). Roles live on `UserProfile`, a OneToOne extension of Django's built-in `User`. A signal auto-creates `UserProfile(role=OPERADOR)` on `User.post_save`. Superusers without a profile bypass role logic and go to `/admin/`.

The home view (`/`) redirects: IT → `/it/tickets/`, OMA/OPERADOR → `/crear/`.

IT-only views use the `@it_required` decorator (defined in `views.py`), which redirects unauthenticated users to login and authenticated non-IT users to `home`. `LoginRateLimitMiddleware` blocks brute-force at `/accounts/login/` (10 failures / 5 min per IP).

### Data Model

`Ticket` is the central model. Phase-2 fields added: `priority`, `category`, `assigned_to`, `started_at`, `resolved_at`, `sla_deadline`, `sla_met`, `sla_warning_sent`.

Supporting models:
- `SLAPolicy` — one row per priority level, defines `resolution_hours`; managed via Django admin
- `TicketWorkSession` — time-tracking records per technician per ticket; auto-managed by signals
- `AuditLog` — immutable record of every field change on a Ticket; written by signals, never directly by views
- `TicketRoutingRule` — maps `(category, area)` → IT technician for auto-assignment at creation; more-specific rule (with area) takes precedence over generic (area="")
- `TicketNote` — internal IT notes, invisible to requesters

### Signal-Driven Side Effects (`tickets/signals.py`)

All ticket lifecycle logic lives in signals, not views. Views only call `ticket.save()`; they must set `ticket._changed_by = request.user` beforehand so signals can attribute the action.

**`pre_save` (`prepare_ticket_before_save`)**:
1. Snapshots `Ticket.objects.get(pk=instance.pk)` into `instance._pre_save_state` for comparison
2. On new tickets: calculates `sla_deadline` from `SLAPolicy`, runs auto-assignment via `TicketRoutingRule`
3. On existing tickets: recalculates SLA and resets `sla_warning_sent` if priority changed; auto-sets `started_at` / `resolved_at` / resets `started_at` based on status transitions

**`post_save` (`handle_ticket_post_save`)**:
1. Writes `AuditLog` entries for any of `("status", "response", "priority", "category", "assigned_to_id")` that changed
2. Opens / closes `TicketWorkSession` based on status transitions (EN_PROCESO → opens, CERRADO/PENDIENTE → closes open sessions)
3. On close: evaluates `sla_met` via `sla.evaluate_sla_met()` and persists via `.update()` (avoiding signal recursion)

The `escalate_tickets` command sets `ticket._escalation = True` to suppress the redundant `FIELD_CHANGE` audit entry when it already writes an `ESCALATED` entry itself.

### Notification System (`tickets/notifications.py`)

Email is sent via Microsoft Graph API (`tickets/graph.py`) using the Client Credentials OAuth flow. The Graph client caches the access token in memory (thread-safe) and renews it 60s before expiry.

`notifications.py` listens on `post_save` and fires emails asynchronously. Dispatch order:
1. If `CELERY_BROKER_URL` is set → `tasks.send_notification_email.delay()` (3 retries, 60s apart)
2. Otherwise → daemon thread (no retries)

If Azure credentials are not configured, `_is_enabled()` returns False and all notification functions return silently — the app works without email.

Triggered events: ticket created (→ IT team), ticket assigned (→ technician), status changed (→ requester), SLA warning (→ technician + IT team, via management command).

### IT Views Summary

| View | URL | Purpose |
|---|---|---|
| `ticket_list` | `/it/tickets/` | Dashboard with filter/search/pagination/bulk actions |
| `respond_ticket` | `/it/respond/<pk>/` | Ticket detail: response form, notes, audit log, work time |
| `technician_dashboard` | `/it/dashboard/` | Personal stats + active tickets for the logged-in technician |
| `it_stats` | `/it/estadisticas/` | Team-wide stats, per-technician breakdown |
| `executive_dashboard` | `/it/ejecutivo/` | Month-over-month KPIs, 30-day trend, at-risk SLA tickets |
| `export_tickets_csv` | `/it/tickets/exportar/` | UTF-8 BOM CSV (Excel-compatible) of all tickets |
| `bulk_action_tickets` | `/it/tickets/bulk/` | POST-only: cerrar / en_proceso / pendiente / asignar |

Charts in the dashboard views use Chart.js data injected as JSON from the view context.

El sistema LAS Cargo IT Service Desk es una aplicación Django para gestión de tickets de soporte IT interno.

El sistema debe proporcionar:

Trazabilidad completa.
Auditoría de acciones.
Control de estados.
Asignación de técnicos.
Historial de comunicaciones.
Reportes y métricas.
Escalabilidad futura.
Portal de usuarios con historial de estados

el Usuario debe Crear tickets, Consultar sus tickets, djuntar archivos  (imágenes, PDFs, logs,etc) , Responder comentarios,Reabrir tickets, y tambien debe poder ver los comentarios que el tenico le envia.


el Técnico IT debe Gestionar tickets asignados, Cambiar estados, Resolver tickets,Agregar comentarios internos, enviar comentarios al usuario,Adjuntar evidencias.

el Administrador IT debe Ver todos los tickets, Asignar técnicos,Gestionar usuarios,Gestionar categorías,Configurar SLA,Consultar auditoría,Ver reportes

Autenticación de Usuarios

El sistema NO utilizará Azure Entra ID ni Microsoft 365 para el inicio de sesión.

Los usuarios deberán autenticarse mediante credenciales creadas y administradas directamente por el administrador del sistema dentro de Django.

Cada usuario tendrá:

Nombre de usuario.
Contraseña.
Nombre completo.
Correo corporativo.
Departamento.
Rol.

Roles disponibles:

Usuario
Técnico IT
Administrador IT

Las contraseñas deberán almacenarse utilizando el sistema de hash nativo de Django.

Nunca se almacenarán contraseñas en texto plano.

Cada usuario deberá tener asociado un correo corporativo de Microsoft 365.

El sistema deberá enviar correos electrónicos automáticos cuando ocurra cualquiera de los siguientes eventos:

Creación de Ticket

Cuando un usuario cree un ticket:

El usuario recibirá una confirmación con el número del ticket.
El técnico asignado recibirá una notificación del nuevo ticket.
Si el ticket no tiene técnico asignado, la notificación será enviada al grupo de soporte IT.
Cuando un ticket sea asignado:
El técnico recibirá una notificación.
El usuario será informado del técnico responsable.
Cuando el estado cambie:
El usuario recibirá una notificación.
El técnico recibirá una notificación cuando el cambio sea realizado por el usuario.
Cuando se agregue un comentario: La otra parte involucrada deberá recibir una notificación.
Cuando el ticket sea marcado como resuelto: El usuario recibirá una notificación para validar la solución.
Cuando el ticket sea cerrado: El usuario recibirá una notificación final de cierre.

Todas las notificaciones enviadas deberán registrarse en la auditoría del sistema.

los Estados del Ticket deben ser una lista desplegable que indiquen ABIERTO,EN PROGRESO,RESUELTO,CERRADO,REAbrido Todo ticket inicia en OPEN.Solo un técnico o administrador puede cambiar el estado El usuario puede reabrir un ticket

las prioridades del Ticket  BAJO,MEDIO,ALTO,CRÍTICO El usuario selecciona la prioridad inicial, solo El técnico puede modificarla,Toda modificación de prioridad debe quedar auditada

El sistema debe calcular automáticamente vencimientos.
Debe generar alertas antes del vencimiento que deben ser enviadas por correo al tecnico asignado
Debe registrar incumplimientos

debe tener Auditoría completa
Creación ticket
Edición ticket
Cambio estado
Cambio prioridad
Asignación técnico
Reasignación técnico
Comentarios
Adjuntos
Reapertura
Resolución
Cierre
Login
Logout
Creación usuario
Cambio contraseña
Desactivación usuario

debe tener Numeración de Tickets el formato debe ser TK-2026-000001 debe ser Único, Secuencial, No reutilizable

para los archivos adjuntos deben ser en 
jpg
jpeg
png
pdf
docx
xlsx
zip
txt
log

para los comentarios deben ser dos 
Comentario Público Visible para usuario y técnicos.
Comentario Interno Visible únicamente para técnicos y administradores

el dasboard del admin debe contener 

Usuario:
Tickets Abiertos
Tickets Resueltos
Tickets Cerrados
Últimos Tickets

Técnico
Tickets Asignados
Tickets Pendientes
Tickets Críticos
Tickets Próximos a vencer SLA

Tickets por categoría
Tickets por técnico
Tickets por prioridad
Cumplimiento SLA
Tiempo promedio de resolución

CSRF habilitado.
Validación de permisos por rol.
Protección contra subida de archivos maliciosos.
Contraseñas hash de Django.
Logs de acceso.
Sesiones seguras.

No se eliminarán tickets físicamente.
No se eliminarán comentarios físicamente.
No se eliminarán auditorías físicamente.

Todo ticket debe tener un responsable asignado.