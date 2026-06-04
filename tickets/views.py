import csv
import json
import logging
import mimetypes
import re
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import (
    AttachmentUploadForm, RoleForm, RoutingRuleForm, SetPasswordAdminForm,
    SLAPolicyForm, TicketCategoryForm, TicketCreateForm,
    TicketResponseForm, UserCommentForm, UserCreateForm, UserEditForm,
)
from .models import (
    AuditLog, Role, RolePermission, SLAPolicy, SystemAuditLog,
    Ticket, TicketAttachment, TicketCategory, TicketNote, TicketRoutingRule, TicketWorkSession, UserProfile,
)
from .notifications import notify_comment_added
from .sla import format_minutes

logger = logging.getLogger("tickets")


# ---------------------------------------------------------------------------
# Helpers de autorización
# ---------------------------------------------------------------------------

def is_it(user):
    if not user.is_authenticated:
        return False
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_it_staff)


def it_required(view_func):
    """
    Unauthenticated users -> login page.
    Authenticated non-IT users -> home (avoids redirect loop to login).
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_it(request.user):
            return redirect("home")
        return view_func(request, *args, **kwargs)
    return wrapper


def permission_required(codename: str):
    """
    Decorator para verificar permisos granulares de RolePermission.
    Requiere que el usuario ya sea IT staff (apila sobre @it_required implícito).
    Los superusers pasan siempre.
    """
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)
            if not is_it(request.user):
                return redirect("home")
            profile = getattr(request.user, "profile", None)
            if not profile or not profile.has_permission(codename):
                messages.error(request, "No tienes permisos para acceder a esta sección.")
                return redirect("ticket_list")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


def _ensure_profile(user):
    """
    Returns the UserProfile (creating it if missing for non-superusers).
    Returns None for Django superusers/staff without a profile → redirect to /admin/.
    """
    profile = getattr(user, "profile", None)
    if profile is None:
        if user.is_superuser or user.is_staff:
            return None
        default_role = Role.objects.filter(code="OPERADOR", is_active=True).first()
        profile, _ = UserProfile.objects.get_or_create(
            user=user, defaults={"role": default_role}
        )
    return profile


# ---------------------------------------------------------------------------
# Vistas de usuario (OMA / OPERADOR)
# ---------------------------------------------------------------------------

@login_required
def home(request):
    profile = _ensure_profile(request.user)
    if profile is None:
        return redirect("/admin/")
    if profile.is_it_staff:
        return redirect("ticket_list")
    return redirect("create_ticket")


@login_required
def create_ticket(request):
    profile = _ensure_profile(request.user)

    if profile is None:
        return redirect("/admin/")
    if profile.is_it_staff:
        return redirect("ticket_list")

    area = profile.role.ticket_area if (profile.role and profile.role.ticket_area) else Ticket.Area.OPERADOR
    form = TicketCreateForm(request.POST or None)

    if form.is_valid():
        ticket = form.save(commit=False)
        ticket.requester   = request.user
        ticket.area        = area
        ticket._changed_by = request.user
        ticket.save()
        logger.info("Ticket #%s creado por '%s' (área: %s, categoría: %s)",
                    ticket.pk, request.user.username, area, ticket.category)
        return redirect("ticket_success", pk=ticket.pk)

    return render(request, "tickets/create_ticket.html", {"form": form, "area": area})


@login_required
def ticket_success(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk, requester=request.user)
    return render(request, "tickets/ticket_success.html", {"ticket": ticket})


@login_required
def my_tickets(request):
    profile = _ensure_profile(request.user)

    if profile is None:
        return redirect("/admin/")
    if profile.is_it_staff:
        return redirect("ticket_list")

    area       = profile.role.ticket_area if (profile.role and profile.role.ticket_area) else Ticket.Area.OPERADOR
    tickets_qs = (
        Ticket.objects
        .filter(requester=request.user, area=area)
        .order_by("-created_at")
    )
    paginator = Paginator(tickets_qs, 15)
    page_obj  = paginator.get_page(request.GET.get("page"))
    return render(request, "tickets/my_tickets.html", {
        "tickets":      page_obj,
        "page_obj":     page_obj,
        "current_area": area,
    })


@login_required
def user_ticket_detail(request, pk):
    profile = _ensure_profile(request.user)
    if profile is None:
        return redirect("/admin/")
    if profile.is_it_staff:
        return redirect("respond_ticket", pk=pk)

    ticket       = get_object_or_404(Ticket, pk=pk, requester=request.user)
    public_notes = ticket.notes.filter(is_public=True).select_related("author").order_by("created_at")
    attachments  = ticket.attachments.select_related("uploaded_by").order_by("created_at")
    comment_form = UserCommentForm()

    status_history = ticket.audit_logs.filter(
        action__in=[
            AuditLog.Action.CREATED,
            AuditLog.Action.STATUS_CHANGE,
            AuditLog.Action.REOPENED,
            AuditLog.Action.ASSIGNED,
        ]
    ).select_related("user").order_by("timestamp")

    return render(request, "tickets/ticket_detail.html", {
        "ticket":          ticket,
        "public_notes":    public_notes,
        "attachments":     attachments,
        "comment_form":    comment_form,
        "attachment_form": AttachmentUploadForm(),
        "can_reopen":      ticket.status == Ticket.Status.RESUELTO,
        "can_comment":     ticket.status not in (Ticket.Status.CERRADO,),
        "status_history":  status_history,
    })


@login_required
def reopen_ticket(request, pk):
    if request.method != "POST":
        return redirect("user_ticket_detail", pk=pk)

    profile = _ensure_profile(request.user)
    if profile is None or profile.is_it_staff:
        return redirect("home")

    ticket = get_object_or_404(Ticket, pk=pk, requester=request.user)

    if ticket.status != Ticket.Status.RESUELTO:
        messages.error(request, "Solo puedes reabrir tickets con estado Resuelto.")
        return redirect("user_ticket_detail", pk=pk)

    ticket.status      = Ticket.Status.REABIERTO
    ticket._changed_by = request.user
    ticket.save()  # signal crea AuditLog.REOPENED automáticamente

    logger.info("Ticket %s reabierto por '%s'", ticket.ticket_number, request.user.username)
    messages.success(request, "Tu ticket fue reabierto. El equipo IT lo revisará pronto.")
    return redirect("user_ticket_detail", pk=pk)


@login_required
def add_user_comment(request, pk):
    if request.method != "POST":
        return redirect("user_ticket_detail", pk=pk)

    profile = _ensure_profile(request.user)
    if profile is None or profile.is_it_staff:
        return redirect("home")

    ticket = get_object_or_404(Ticket, pk=pk, requester=request.user)

    if ticket.status == Ticket.Status.CERRADO:
        messages.error(request, "No puedes comentar en un ticket cerrado.")
        return redirect("user_ticket_detail", pk=pk)

    form = UserCommentForm(request.POST)
    if form.is_valid():
        note           = form.save(commit=False)
        note.ticket    = ticket
        note.author    = request.user
        note.is_public = True
        note.save()

        AuditLog.objects.create(
            ticket=ticket,
            user=request.user,
            action=AuditLog.Action.COMMENT_ADDED,
            new_value=note.content[:200],
        )
        notify_comment_added(ticket, request.user, note.content, is_public=True)
        messages.success(request, "Comentario enviado.")

    return redirect("user_ticket_detail", pk=pk)


# ---------------------------------------------------------------------------
# Adjuntos
# ---------------------------------------------------------------------------

@login_required
def upload_attachment(request, pk):
    if request.method != "POST":
        return redirect("home")

    profile = _ensure_profile(request.user)
    if profile is None:
        return redirect("/admin/")

    if profile.is_it_staff:
        ticket        = get_object_or_404(Ticket, pk=pk)
        redirect_view = "respond_ticket"
    else:
        ticket        = get_object_or_404(Ticket, pk=pk, requester=request.user)
        redirect_view = "user_ticket_detail"
        if ticket.status == Ticket.Status.CERRADO:
            messages.error(request, "No puedes adjuntar archivos a un ticket cerrado.")
            return redirect(redirect_view, pk=pk)

    form = AttachmentUploadForm(request.POST, request.FILES)
    if form.is_valid():
        f = form.cleaned_data["file"]
        TicketAttachment.objects.create(
            ticket        = ticket,
            uploaded_by   = request.user,
            file          = f,
            original_name = f.name,
            file_size     = f.size,
            content_type  = getattr(f, "content_type", "") or "",
        )
        AuditLog.objects.create(
            ticket    = ticket,
            user      = request.user,
            action    = AuditLog.Action.ATTACHMENT_ADDED,
            new_value = f.name,
        )
        messages.success(request, f"Archivo '{f.name}' adjuntado correctamente.")
    else:
        for err_list in form.errors.values():
            for err in err_list:
                messages.error(request, err)

    return redirect(redirect_view, pk=pk)


@login_required
def delete_attachment(request, attachment_pk):
    if request.method != "POST":
        return redirect("home")

    attachment = get_object_or_404(TicketAttachment, pk=attachment_pk)
    ticket     = attachment.ticket
    profile    = _ensure_profile(request.user)

    if profile is None:
        return redirect("/admin/")

    if profile.is_it_staff:
        redirect_view = "respond_ticket"
    elif ticket.requester == request.user:
        if ticket.status == Ticket.Status.CERRADO:
            messages.error(request, "No puedes eliminar adjuntos de un ticket cerrado.")
            return redirect("user_ticket_detail", pk=ticket.pk)
        redirect_view = "user_ticket_detail"
    else:
        messages.error(request, "No tienes permiso para eliminar este adjunto.")
        return redirect("home")

    original_name = attachment.original_name
    attachment.file.delete(save=False)
    attachment.delete()

    AuditLog.objects.create(
        ticket    = ticket,
        user      = request.user,
        action    = AuditLog.Action.ATTACHMENT_DELETED,
        old_value = original_name,
    )
    messages.success(request, f"Adjunto '{original_name}' eliminado.")
    return redirect(redirect_view, pk=ticket.pk)


@login_required
def download_attachment(request, attachment_pk):
    attachment = get_object_or_404(TicketAttachment, pk=attachment_pk)
    ticket     = attachment.ticket
    profile    = _ensure_profile(request.user)

    if profile is None:
        return redirect("/admin/")

    if not profile.is_it_staff and ticket.requester != request.user:
        messages.error(request, "No tienes permiso para descargar este adjunto.")
        return redirect("home")

    try:
        fh           = attachment.file.open("rb")
        response     = FileResponse(fh)
        content_type = (
            attachment.content_type
            or mimetypes.guess_type(attachment.original_name)[0]
            or "application/octet-stream"
        )
        safe_name = re.sub(r'[^\w\s\-.]', '_', attachment.original_name)
        response["Content-Type"]        = content_type
        response["Content-Disposition"] = f'attachment; filename="{safe_name}"'
        return response
    except FileNotFoundError:
        messages.error(request, "El archivo no se encuentra en el servidor.")
        if profile.is_it_staff:
            return redirect("respond_ticket", pk=ticket.pk)
        return redirect("user_ticket_detail", pk=ticket.pk)


# ---------------------------------------------------------------------------
# Vistas de IT
# ---------------------------------------------------------------------------

@it_required
def ticket_list(request):
    status_f      = request.GET.get("status", "")
    priority_f    = request.GET.get("priority", "")
    category_f    = request.GET.get("category", "")
    assigned_f    = request.GET.get("assigned_to", "")
    search_q      = request.GET.get("q", "").strip()

    tickets = Ticket.objects.select_related("requester", "assigned_to").order_by("-created_at")

    if status_f:
        tickets = tickets.filter(status=status_f)
    if priority_f:
        tickets = tickets.filter(priority=priority_f)
    if category_f:
        tickets = tickets.filter(category=category_f)
    if assigned_f == "unassigned":
        tickets = tickets.filter(assigned_to__isnull=True)
    elif assigned_f:
        tickets = tickets.filter(assigned_to__id=assigned_f)
    if search_q:
        tickets = tickets.filter(
            Q(title__icontains=search_q)
            | Q(description__icontains=search_q)
            | Q(requester__username__icontains=search_q)
            | Q(requester__first_name__icontains=search_q)
            | Q(requester__last_name__icontains=search_q)
        )

    counts = Ticket.objects.aggregate(
        total        = Count("id"),
        abiertos     = Count("id", filter=Q(status=Ticket.Status.ABIERTO)),
        en_proceso   = Count("id", filter=Q(status=Ticket.Status.EN_PROCESO)),
        resueltos    = Count("id", filter=Q(status=Ticket.Status.RESUELTO)),
        cerrados     = Count("id", filter=Q(status=Ticket.Status.CERRADO)),
        reabiertos   = Count("id", filter=Q(status=Ticket.Status.REABIERTO)),
        sla_vencidos = Count("id", filter=Q(sla_met=False)),
        sin_asignar  = Count("id", filter=Q(assigned_to__isnull=True)),
    )

    paginator      = Paginator(tickets, 20)
    page_obj       = paginator.get_page(request.GET.get("page"))
    it_technicians = User.objects.filter(profile__role__is_it_staff=True).order_by("first_name", "username")

    return render(request, "tickets/ticket_list.html", {
        "tickets":          page_obj,
        "page_obj":         page_obj,
        **counts,
        "status_choices":   Ticket.Status.choices,
        "priority_choices": Ticket.Priority.choices,
        "category_choices": [("", "Categoría")] + TicketCategory.active_choices(),
        "it_technicians":   it_technicians,
        "filters": {
            "status":      status_f,
            "priority":    priority_f,
            "category":    category_f,
            "assigned_to": assigned_f,
            "q":           search_q,
        },
    })


@it_required
def respond_ticket(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)

    # Notes split by visibility — ordered oldest-first for chronological display
    public_notes   = ticket.notes.filter(is_public=True).select_related("author").order_by("created_at")
    internal_notes = ticket.notes.filter(is_public=False).select_related("author").order_by("created_at")
    attachments    = ticket.attachments.select_related("uploaded_by").order_by("created_at")
    audit_logs     = ticket.audit_logs.select_related("user").order_by("-timestamp")[:25]

    # Carga activa por técnico (tickets abiertos/en proceso)
    _active = [Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO]
    tech_workload = dict(
        Ticket.objects
        .filter(assigned_to__isnull=False, status__in=_active)
        .values("assigned_to_id")
        .annotate(n=Count("id"))
        .values_list("assigned_to_id", "n")
    )

    work_agg = ticket.work_sessions.filter(ended_at__isnull=False).aggregate(
        total=Sum(
            ExpressionWrapper(
                F("ended_at") - F("started_at"),
                output_field=DurationField(),
            )
        )
    )
    work_td            = work_agg["total"]
    total_work_minutes = int(work_td.total_seconds() / 60) if work_td else 0

    if request.method == "POST":
        note_type = request.POST.get("add_note", "")

        # Branch: add a comment or internal note
        if note_type in ("public", "internal"):
            content   = request.POST.get("note_content", "").strip()
            is_public = note_type == "public"
            if content:
                TicketNote.objects.create(
                    ticket    = ticket,
                    author    = request.user,
                    content   = content,
                    is_public = is_public,
                )
                AuditLog.objects.create(
                    ticket    = ticket,
                    user      = request.user,
                    action    = AuditLog.Action.COMMENT_ADDED,
                    new_value = ("público" if is_public else "interno") + ": " + content[:200],
                )
                if is_public:
                    notify_comment_added(ticket, request.user, content, is_public=True)
                label = "Comentario enviado al usuario." if is_public else "Nota interna agregada."
                messages.success(request, label)
                logger.info(
                    "Ticket %s — %s por '%s'",
                    ticket.ticket_number or ticket.pk,
                    "comentario público" if is_public else "nota interna",
                    request.user.username,
                )
            return redirect("respond_ticket", pk=pk)

        # Branch: update ticket fields (status, priority, response…)
        form = TicketResponseForm(request.POST, instance=ticket)
        if form.is_valid():
            old_status         = ticket.status
            ticket             = form.save(commit=False)
            ticket._changed_by = request.user
            ticket.save()
            logger.info(
                "Ticket %s actualizado por '%s': %s -> %s",
                ticket.ticket_number or ticket.pk,
                request.user.username,
                old_status,
                ticket.status,
            )
            return redirect("ticket_list")
    else:
        form = TicketResponseForm(instance=ticket)

    it_technicians       = User.objects.filter(profile__role__is_it_staff=True).order_by("first_name", "username")
    it_techs_with_load   = [(u, tech_workload.get(u.pk, 0)) for u in it_technicians]

    return render(request, "tickets/respond_ticket.html", {
        "form":                form,
        "ticket":              ticket,
        "audit_logs":          audit_logs,
        "public_notes":        public_notes,
        "internal_notes":      internal_notes,
        "attachments":         attachments,
        "attachment_form":     AttachmentUploadForm(),
        "tech_workload":       tech_workload,
        "it_techs_with_load":  it_techs_with_load,
        "total_work_minutes":  total_work_minutes,
        "total_work_fmt":      format_minutes(total_work_minutes),
    })


# ---------------------------------------------------------------------------
# Dashboard personal del técnico
# ---------------------------------------------------------------------------

@it_required
def technician_dashboard(request):
    user        = request.user
    now         = timezone.now()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    my_tickets_month = Ticket.objects.filter(
        assigned_to=user, created_at__gte=month_start
    )

    stats = my_tickets_month.aggregate(
        total         = Count("id"),
        cerrados      = Count("id", filter=Q(status=Ticket.Status.CERRADO)),
        resueltos     = Count("id", filter=Q(status=Ticket.Status.RESUELTO)),
        en_proceso    = Count("id", filter=Q(status=Ticket.Status.EN_PROCESO)),
        abiertos      = Count("id", filter=Q(status=Ticket.Status.ABIERTO)),
        reabiertos    = Count("id", filter=Q(status=Ticket.Status.REABIERTO)),
        sla_cumplidos = Count("id", filter=Q(sla_met=True)),
        sla_vencidos  = Count("id", filter=Q(sla_met=False)),
    )

    avg_qs = my_tickets_month.filter(
        started_at__isnull=False,
        resolved_at__isnull=False,
        status=Ticket.Status.CERRADO,
    ).aggregate(
        avg=Avg(
            ExpressionWrapper(
                F("resolved_at") - F("started_at"),
                output_field=DurationField(),
            )
        )
    )
    avg_td      = avg_qs["avg"]
    avg_minutes = int(avg_td.total_seconds() / 60) if avg_td else None

    work_agg = (
        TicketWorkSession.objects
        .filter(technician=user, started_at__gte=month_start, ended_at__isnull=False)
        .aggregate(
            total=Sum(
                ExpressionWrapper(
                    F("ended_at") - F("started_at"),
                    output_field=DurationField(),
                )
            )
        )
    )
    work_td            = work_agg["total"]
    total_work_minutes = int(work_td.total_seconds() / 60) if work_td else 0

    _active_statuses = [Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO]

    active_tickets = (
        Ticket.objects
        .filter(assigned_to=user, status__in=_active_statuses)
        .order_by("-created_at")[:10]
    )

    criticos_activos = Ticket.objects.filter(
        assigned_to=user,
        priority=Ticket.Priority.CRITICAL,
        status__in=_active_statuses,
    ).count()

    four_h     = now + timedelta(hours=4)
    sla_riesgo = list(
        Ticket.objects.filter(
            assigned_to=user,
            status__in=_active_statuses,
            sla_deadline__lte=four_h,
        ).order_by("sla_deadline")[:5]
    )

    stats["pendientes"] = stats["abiertos"] + stats["reabiertos"]

    by_category = list(
        my_tickets_month.values("category").annotate(count=Count("id")).order_by("-count")
    )
    cat_labels = [TicketCategory.get_label(c["category"]) for c in by_category]
    cat_data   = [c["count"] for c in by_category]

    by_priority = list(
        my_tickets_month.values("priority").annotate(count=Count("id")).order_by("-count")
    )
    pri_labels = [dict(Ticket.Priority.choices).get(p["priority"], p["priority"]) for p in by_priority]
    pri_data   = [p["count"] for p in by_priority]

    return render(request, "tickets/dashboard.html", {
        "stats":              stats,
        "avg_minutes":        avg_minutes,
        "avg_fmt":            format_minutes(avg_minutes),
        "total_work_minutes": total_work_minutes,
        "total_work_fmt":     format_minutes(total_work_minutes),
        "active_tickets":     active_tickets,
        "criticos_activos":   criticos_activos,
        "sla_riesgo":         sla_riesgo,
        "month_label":        now.strftime("%B %Y").capitalize(),
        "chart_category":     json.dumps({"labels": cat_labels, "data": cat_data}),
        "chart_priority":     json.dumps({"labels": pri_labels, "data": pri_data}),
    })


# ---------------------------------------------------------------------------
# Estadísticas globales para el equipo IT
# ---------------------------------------------------------------------------

@it_required
def it_stats(request):
    from django.utils.dateparse import parse_date as _pd
    now         = timezone.now()
    today       = now.date()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()

    date_from_s = request.GET.get("date_from", month_start.strftime("%Y-%m-%d"))
    date_to_s   = request.GET.get("date_to",   today.strftime("%Y-%m-%d"))
    date_from   = _pd(date_from_s) or month_start
    date_to     = _pd(date_to_s)   or today

    period_qs = Ticket.objects.filter(
        created_at__date__gte=date_from,
        created_at__date__lte=date_to,
    )

    overall = period_qs.aggregate(
        total         = Count("id"),
        abiertos      = Count("id", filter=Q(status=Ticket.Status.ABIERTO)),
        en_proceso    = Count("id", filter=Q(status=Ticket.Status.EN_PROCESO)),
        resueltos     = Count("id", filter=Q(status=Ticket.Status.RESUELTO)),
        cerrados      = Count("id", filter=Q(status=Ticket.Status.CERRADO)),
        reabiertos    = Count("id", filter=Q(status=Ticket.Status.REABIERTO)),
        sla_cumplidos = Count("id", filter=Q(sla_met=True)),
        sla_vencidos  = Count("id", filter=Q(sla_met=False)),
        avg_td        = Avg(
            ExpressionWrapper(F("resolved_at") - F("started_at"), output_field=DurationField()),
            filter=Q(resolved_at__isnull=False, started_at__isnull=False),
        ),
    )
    overall["pendientes"] = overall["abiertos"] + overall["reabiertos"]
    _avg = overall.pop("avg_td")
    overall["avg_fmt"] = format_minutes(int(_avg.total_seconds() / 60) if _avg else None)

    # SLA compliance broken down by priority (closed tickets in period)
    sla_by_priority = []
    for pval, plabel in Ticket.Priority.choices:
        s = period_qs.filter(priority=pval, status=Ticket.Status.CERRADO).aggregate(
            cumplidos = Count("id", filter=Q(sla_met=True)),
            vencidos  = Count("id", filter=Q(sla_met=False)),
        )
        total_p = s["cumplidos"] + s["vencidos"]
        sla_by_priority.append({
            "label":     plabel,
            "cumplidos": s["cumplidos"],
            "vencidos":  s["vencidos"],
            "total":     total_p,
            "rate":      round(s["cumplidos"] / total_p * 100) if total_p else None,
        })

    tech_stats = (
        period_qs.filter(assigned_to__isnull=False)
        .values("assigned_to__id", "assigned_to__username",
                "assigned_to__first_name", "assigned_to__last_name")
        .annotate(
            total          = Count("id"),
            cerrados       = Count("id", filter=Q(status=Ticket.Status.CERRADO)),
            sla_cumplidos  = Count("id", filter=Q(sla_met=True)),
            sla_vencidos   = Count("id", filter=Q(sla_met=False)),
            avg_resolution = Avg(
                ExpressionWrapper(F("resolved_at") - F("started_at"), output_field=DurationField()),
                filter=Q(resolved_at__isnull=False, started_at__isnull=False),
            ),
        )
        .order_by("-cerrados")
    )

    tech_list = []
    for t in tech_stats:
        avg_td = t["avg_resolution"]
        t["avg_fmt"] = format_minutes(int(avg_td.total_seconds() / 60) if avg_td else None)
        name = (t["assigned_to__first_name"] + " " + t["assigned_to__last_name"]).strip()
        t["display_name"] = name or t["assigned_to__username"]
        tech_list.append(t)

    by_category = list(period_qs.values("category").annotate(count=Count("id")).order_by("-count"))
    cat_labels  = [TicketCategory.get_label(c["category"]) for c in by_category]
    cat_data    = [c["count"] for c in by_category]

    by_priority = list(period_qs.values("priority").annotate(count=Count("id")).order_by("-count"))
    pri_labels  = [dict(Ticket.Priority.choices).get(p["priority"], p["priority"]) for p in by_priority]
    pri_data    = [p["count"] for p in by_priority]

    by_area    = list(period_qs.values("area").annotate(count=Count("id")).order_by("-count"))
    area_labels = [dict(Ticket.Area.choices).get(a["area"], a["area"]) for a in by_area]
    area_data   = [a["count"] for a in by_area]

    return render(request, "tickets/it_stats.html", {
        "overall":         overall,
        "tech_list":       tech_list,
        "sla_by_priority": sla_by_priority,
        "date_from":       date_from_s,
        "date_to":         date_to_s,
        "period_label":    f"{date_from.strftime('%d/%m/%Y')} — {date_to.strftime('%d/%m/%Y')}",
        "chart_category":  json.dumps({"labels": cat_labels, "data": cat_data}),
        "chart_priority":  json.dumps({"labels": pri_labels, "data": pri_data}),
        "chart_area":      json.dumps({"labels": area_labels, "data": area_data}),
    })


# ---------------------------------------------------------------------------
# Dashboard ejecutivo (comparativa mes a mes + tendencia 30 días)
# ---------------------------------------------------------------------------

@it_required
def executive_dashboard(request):
    now   = timezone.now()
    today = now.date()

    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if month_start.month == 1:
        prev_month_start = month_start.replace(year=month_start.year - 1, month=12)
    else:
        prev_month_start = month_start.replace(month=month_start.month - 1)

    def _month_kpis(qs):
        return qs.aggregate(
            total         = Count("id"),
            cerrados      = Count("id", filter=Q(status=Ticket.Status.CERRADO)),
            resueltos     = Count("id", filter=Q(status=Ticket.Status.RESUELTO)),
            abiertos      = Count("id", filter=Q(status=Ticket.Status.ABIERTO)),
            en_proceso    = Count("id", filter=Q(status=Ticket.Status.EN_PROCESO)),
            reabiertos    = Count("id", filter=Q(status=Ticket.Status.REABIERTO)),
            sla_cumplidos = Count("id", filter=Q(sla_met=True)),
            sla_vencidos  = Count("id", filter=Q(sla_met=False)),
        )

    current  = _month_kpis(Ticket.objects.filter(created_at__gte=month_start))
    previous = _month_kpis(Ticket.objects.filter(
        created_at__gte=prev_month_start, created_at__lt=month_start
    ))
    current["pendientes"]  = current["abiertos"]  + current["reabiertos"]
    previous["pendientes"] = previous["abiertos"]  + previous["reabiertos"]

    def _pct_delta(curr, prev):
        if not prev:
            return None
        return round((curr - prev) / prev * 100, 1)

    kpi_deltas = {
        "total":    _pct_delta(current["total"],         previous["total"]),
        "cerrados": _pct_delta(current["cerrados"],      previous["cerrados"]),
        "sla":      _pct_delta(current["sla_cumplidos"], previous["sla_cumplidos"]),
    }

    thirty_days_ago = today - timedelta(days=29)
    daily_qs = (
        Ticket.objects
        .filter(created_at__date__gte=thirty_days_ago)
        .annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )
    daily_map    = {row["day"]: row["count"] for row in daily_qs}
    trend_labels = []
    trend_data   = []
    for i in range(30):
        d = thirty_days_ago + timedelta(days=i)
        trend_labels.append(d.strftime("%d/%m"))
        trend_data.append(daily_map.get(d, 0))

    sla_counts = Ticket.objects.filter(status=Ticket.Status.CERRADO).aggregate(
        cumplidos = Count("id", filter=Q(sla_met=True)),
        vencidos  = Count("id", filter=Q(sla_met=False)),
        sin_datos = Count("id", filter=Q(sla_met__isnull=True)),
    )
    total_with_sla = sla_counts["cumplidos"] + sla_counts["vencidos"]
    sla_pct = round(sla_counts["cumplidos"] / total_with_sla * 100, 1) if total_with_sla else None

    by_category = list(
        Ticket.objects.values("category").annotate(count=Count("id")).order_by("-count")
    )
    cat_labels = [TicketCategory.get_label(c["category"]) for c in by_category]
    cat_data   = [c["count"] for c in by_category]

    four_h  = now + timedelta(hours=4)
    at_risk = (
        Ticket.objects
        .filter(
            status__in=[Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO],
            sla_deadline__lte=four_h,
        )
        .select_related("assigned_to", "requester")
        .order_by("sla_deadline")[:10]
    )

    return render(request, "tickets/executive_dashboard.html", {
        "current":          current,
        "previous":         previous,
        "kpi_deltas":       kpi_deltas,
        "sla_pct":          sla_pct,
        "sla_counts":       sla_counts,
        "at_risk":          at_risk,
        "month_label":      now.strftime("%B %Y").capitalize(),
        "prev_month_label": prev_month_start.strftime("%B %Y").capitalize(),
        "chart_trend":      json.dumps({"labels": trend_labels, "data": trend_data}),
        "chart_sla":        json.dumps({
            "labels": ["SLA cumplido", "SLA vencido", "Sin datos"],
            "data":   [sla_counts["cumplidos"], sla_counts["vencidos"], sla_counts["sin_datos"]],
        }),
        "chart_category":   json.dumps({"labels": cat_labels, "data": cat_data}),
    })


# ---------------------------------------------------------------------------
# Exportar todos los tickets a CSV (compatible con Excel)
# ---------------------------------------------------------------------------

@it_required
def export_tickets_csv(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="tickets_lascargo.csv"'
    response.write("﻿")  # BOM para Excel

    writer = csv.writer(response)
    writer.writerow([
        "Número", "ID", "Título", "Área", "Estado", "Prioridad", "Categoría",
        "Solicitante", "Técnico asignado",
        "Fecha creación", "Fecha inicio trabajo", "Fecha resolución",
        "Horas resolución", "SLA deadline", "SLA cumplido",
        "Descripción", "Respuesta IT",
    ])

    status_map   = dict(Ticket.Status.choices)
    priority_map = dict(Ticket.Priority.choices)
    category_map = dict(TicketCategory.objects.values_list("code", "name"))
    area_map     = dict(Ticket.Area.choices)

    for t in Ticket.objects.select_related("requester", "assigned_to").order_by("-created_at"):
        assignee = ""
        if t.assigned_to:
            assignee = t.assigned_to.get_full_name() or t.assigned_to.username

        sla_met = ""
        if t.sla_met is True:
            sla_met = "Sí"
        elif t.sla_met is False:
            sla_met = "No"

        resolution_hours = ""
        if t.started_at and t.resolved_at:
            td = t.resolved_at - t.started_at
            resolution_hours = round(td.total_seconds() / 3600, 2)

        writer.writerow([
            t.ticket_number or f"#{t.id}",
            t.id,
            t.title,
            area_map.get(t.area, t.area),
            status_map.get(t.status, t.status),
            priority_map.get(t.priority, t.priority),
            category_map.get(t.category, t.category),
            t.requester.get_full_name() or t.requester.username,
            assignee,
            t.created_at.strftime("%Y-%m-%d %H:%M"),
            t.started_at.strftime("%Y-%m-%d %H:%M") if t.started_at else "",
            t.resolved_at.strftime("%Y-%m-%d %H:%M") if t.resolved_at else "",
            resolution_hours,
            t.sla_deadline.strftime("%Y-%m-%d %H:%M") if t.sla_deadline else "",
            sla_met,
            t.description,
            t.response,
        ])

    return response


# ---------------------------------------------------------------------------
# Acciones masivas sobre tickets seleccionados
# ---------------------------------------------------------------------------

@it_required
def bulk_action_tickets(request):
    if request.method != "POST":
        return redirect("ticket_list")

    action     = request.POST.get("bulk_action", "")
    ticket_ids = request.POST.getlist("ticket_ids")

    # Reconstruir parámetros de filtro para preservarlos en el redirect
    filter_params = {}
    for key in ("filter_q", "filter_status", "filter_priority", "filter_category", "filter_assigned_to"):
        val = request.POST.get(key, "").strip()
        if val:
            filter_params[key.replace("filter_", "")] = val

    def _redirect_back():
        qs = urlencode(filter_params)
        return redirect(f"/it/tickets/?{qs}" if qs else "/it/tickets/")

    if not ticket_ids or not action:
        return _redirect_back()

    tickets_qs = Ticket.objects.filter(pk__in=ticket_ids)

    if action == "cerrar":
        for t in tickets_qs.exclude(status=Ticket.Status.CERRADO):
            t._changed_by = request.user
            t.status      = Ticket.Status.CERRADO
            t.save()
        logger.info("Acción masiva CERRAR: %d ticket(s) por '%s'", len(ticket_ids), request.user.username)

    elif action == "en_proceso":
        for t in tickets_qs.exclude(status=Ticket.Status.EN_PROCESO):
            t._changed_by = request.user
            t.status      = Ticket.Status.EN_PROCESO
            t.save()
        logger.info("Acción masiva EN_PROCESO: %d ticket(s) por '%s'", len(ticket_ids), request.user.username)

    elif action == "abierto":
        for t in tickets_qs.exclude(status=Ticket.Status.ABIERTO):
            t._changed_by = request.user
            t.status      = Ticket.Status.ABIERTO
            t.save()
        logger.info("Acción masiva ABIERTO: %d ticket(s) por '%s'", len(ticket_ids), request.user.username)

    elif action.startswith("asignar_"):
        tech_id = action.split("_", 1)[1]
        try:
            tech = User.objects.get(pk=tech_id, profile__role__is_it_staff=True)
        except User.DoesNotExist:
            return _redirect_back()
        for t in tickets_qs:
            t._changed_by = request.user
            t.assigned_to = tech
            t.save()
        logger.info("Acción masiva ASIGNAR->%s: %d ticket(s) por '%s'",
                    tech.username, len(ticket_ids), request.user.username)

    return _redirect_back()


# ---------------------------------------------------------------------------
# Gestión de usuarios
# ---------------------------------------------------------------------------

@permission_required("usuarios.gestionar")
def user_list(request):
    search_q = request.GET.get("q", "").strip()
    role_f   = request.GET.get("role", "")
    active_f = request.GET.get("active", "")

    users_qs = (
        User.objects
        .select_related("profile__role")
        .order_by("first_name", "last_name", "username")
    )

    if search_q:
        users_qs = users_qs.filter(
            Q(username__icontains=search_q)
            | Q(first_name__icontains=search_q)
            | Q(last_name__icontains=search_q)
            | Q(email__icontains=search_q)
        )
    if role_f:
        users_qs = users_qs.filter(profile__role__pk=role_f)
    if active_f == "1":
        users_qs = users_qs.filter(is_active=True)
    elif active_f == "0":
        users_qs = users_qs.filter(is_active=False)

    paginator = Paginator(users_qs, 20)
    page_obj  = paginator.get_page(request.GET.get("page"))
    roles     = Role.objects.filter(is_active=True).order_by("name")

    return render(request, "tickets/users/user_list.html", {
        "users":    page_obj,
        "page_obj": page_obj,
        "roles":    roles,
        "filters":  {"q": search_q, "role": role_f, "active": active_f},
        "total":    users_qs.count(),
    })


@permission_required("usuarios.gestionar")
def user_create(request):
    form = UserCreateForm(request.POST or None)
    if form.is_valid():
        cd   = form.cleaned_data
        user = User.objects.create_user(
            username   = cd["username"],
            email      = cd["email"],
            password   = cd["password1"],
            first_name = cd["first_name"],
            last_name  = cd["last_name"],
        )
        # La señal create_user_profile ya creó el perfil; lo actualizamos
        profile            = user.profile
        profile.role       = cd["role"]
        profile.department = cd.get("department", "")
        profile.save()

        SystemAuditLog.objects.create(
            action      = SystemAuditLog.Action.USER_CREATED,
            user        = request.user,
            target_user = user,
            details     = f"Rol: {cd['role'].name} | Depto: {cd.get('department', '')}",
        )
        logger.info("Usuario '%s' creado por '%s'", user.username, request.user.username)
        messages.success(request, f"Usuario {user.get_full_name() or user.username} creado correctamente.")
        return redirect("user_list")

    return render(request, "tickets/users/user_form.html", {
        "form":  form,
        "title": "Crear nuevo usuario",
        "btn":   "Crear usuario",
    })


@permission_required("usuarios.gestionar")
def user_edit(request, pk):
    target = get_object_or_404(User.objects.select_related("profile__role"), pk=pk)

    initial = {
        "first_name": target.first_name,
        "last_name":  target.last_name,
        "email":      target.email,
        "department": getattr(target.profile, "department", ""),
        "role":       getattr(target.profile, "role", None),
    }
    form = UserEditForm(request.POST or None, user_pk=pk, initial=initial)

    if form.is_valid():
        cd                     = form.cleaned_data
        target.first_name      = cd["first_name"]
        target.last_name       = cd["last_name"]
        target.email           = cd["email"]
        target.save()

        profile            = target.profile
        profile.role       = cd["role"]
        profile.department = cd.get("department", "")
        profile.save()

        SystemAuditLog.objects.create(
            action      = SystemAuditLog.Action.USER_UPDATED,
            user        = request.user,
            target_user = target,
            details     = f"Rol: {cd['role'].name} | Depto: {cd.get('department', '')}",
        )
        logger.info("Usuario '%s' editado por '%s'", target.username, request.user.username)
        messages.success(request, f"Usuario {target.get_full_name() or target.username} actualizado.")
        return redirect("user_list")

    return render(request, "tickets/users/user_form.html", {
        "form":        form,
        "target_user": target,
        "title":       f"Editar usuario: {target.get_full_name() or target.username}",
        "btn":         "Guardar cambios",
    })


@permission_required("usuarios.gestionar")
def user_toggle_active(request, pk):
    """Activa o desactiva un usuario (soft-delete). No permite auto-desactivarse."""
    if request.method != "POST":
        return redirect("user_list")

    target = get_object_or_404(User, pk=pk)

    if target == request.user:
        messages.error(request, "No puedes desactivar tu propia cuenta.")
        return redirect("user_list")

    target.is_active = not target.is_active
    target.save(update_fields=["is_active"])

    if target.is_active:
        action  = SystemAuditLog.Action.USER_ACTIVATED
        verb    = "activado"
    else:
        action  = SystemAuditLog.Action.USER_DEACTIVATED
        verb    = "desactivado"

    SystemAuditLog.objects.create(
        action      = action,
        user        = request.user,
        target_user = target,
    )
    logger.info("Usuario '%s' %s por '%s'", target.username, verb, request.user.username)
    messages.success(request, f"Usuario {target.get_full_name() or target.username} {verb}.")
    return redirect("user_list")


@permission_required("usuarios.gestionar")
def user_change_password(request, pk):
    target = get_object_or_404(User, pk=pk)
    form   = SetPasswordAdminForm(request.POST or None)

    if form.is_valid():
        target.set_password(form.cleaned_data["new_password1"])
        target.save()

        SystemAuditLog.objects.create(
            action      = SystemAuditLog.Action.PASSWORD_CHANGED,
            user        = request.user,
            target_user = target,
        )
        logger.info("Contraseña de '%s' cambiada por '%s'", target.username, request.user.username)
        messages.success(request, f"Contraseña de {target.get_full_name() or target.username} actualizada.")
        return redirect("user_list")

    return render(request, "tickets/users/user_change_password.html", {
        "form":        form,
        "target_user": target,
    })


# ---------------------------------------------------------------------------
# Gestión de roles
# ---------------------------------------------------------------------------

def _permissions_by_module():
    """Retorna permisos agrupados por módulo: [(label, [RolePermission, ...])]."""
    module_labels = dict(RolePermission.Module.choices)
    groups = {}
    for perm in RolePermission.objects.order_by("module", "action"):
        label = module_labels.get(perm.module, perm.module)
        groups.setdefault(label, []).append(perm)
    return list(groups.items())


@it_required
def role_list(request):
    roles = (
        Role.objects
        .prefetch_related("permissions", "users")
        .order_by("name")
    )
    return render(request, "tickets/roles/role_list.html", {
        "roles":        roles,
        "active_count": sum(1 for r in roles if r.is_active),
    })


@permission_required("roles.gestionar")
def role_create(request):
    form = RoleForm(request.POST or None)
    if form.is_valid():
        role = form.save()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.ROLE_CREATED,
            user=request.user,
            details=f"Rol: {role.name} (código: {role.code})",
        )
        logger.info("Rol '%s' creado por '%s'", role.name, request.user.username)
        return redirect("role_list")
    return render(request, "tickets/roles/role_form.html", {
        "form":                  form,
        "title":                 "Crear nuevo rol",
        "btn_label":             "Crear rol",
        "permissions_by_module": _permissions_by_module(),
    })


@permission_required("roles.gestionar")
def role_edit(request, pk):
    role = get_object_or_404(Role, pk=pk)
    form = RoleForm(request.POST or None, instance=role)
    if form.is_valid():
        form.save()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.ROLE_UPDATED,
            user=request.user,
            details=f"Rol: {role.name} (código: {role.code})",
        )
        logger.info("Rol '%s' editado por '%s'", role.name, request.user.username)
        return redirect("role_list")
    return render(request, "tickets/roles/role_form.html", {
        "form":                  form,
        "role":                  role,
        "title":                 f"Editar rol: {role.name}",
        "btn_label":             "Guardar cambios",
        "permissions_by_module": _permissions_by_module(),
    })


@permission_required("roles.gestionar")
def role_delete(request, pk):
    role = get_object_or_404(Role, pk=pk)
    if request.method == "POST":
        name = role.name
        code = role.code
        role.delete()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.ROLE_DELETED,
            user=request.user,
            details=f"Rol eliminado: {name} (código: {code})",
        )
        logger.info("Rol '%s' eliminado por '%s'", name, request.user.username)
        return redirect("role_list")
    return render(request, "tickets/roles/role_confirm_delete.html", {"role": role})


@permission_required("roles.gestionar")
def role_toggle(request, pk):
    if request.method != "POST":
        return redirect("role_list")
    role = get_object_or_404(Role, pk=pk)
    role.is_active = not role.is_active
    role.save(update_fields=["is_active", "updated_at"])
    state = "activado" if role.is_active else "desactivado"
    logger.info("Rol '%s' %s por '%s'", role.name, state, request.user.username)
    return redirect("role_list")


# ---------------------------------------------------------------------------
# Registro de auditoría global (ticket-level + sistema)
# ---------------------------------------------------------------------------

@it_required
def audit_log(request):
    log_type    = request.GET.get("tipo", "ticket")
    action_f    = request.GET.get("action", "")
    user_f      = request.GET.get("user", "").strip()
    date_from_f = request.GET.get("date_from", "")
    date_to_f   = request.GET.get("date_to", "")

    if log_type == "sistema":
        qs = SystemAuditLog.objects.select_related("user", "target_user").order_by("-timestamp")
        if action_f:
            qs = qs.filter(action=action_f)
        if user_f:
            qs = qs.filter(
                Q(user__username__icontains=user_f)
                | Q(user__first_name__icontains=user_f)
                | Q(user__last_name__icontains=user_f)
                | Q(target_user__username__icontains=user_f)
                | Q(target_user__first_name__icontains=user_f)
                | Q(target_user__last_name__icontains=user_f)
            )
        action_choices = SystemAuditLog.Action.choices
    else:
        qs = AuditLog.objects.select_related("user", "ticket").order_by("-timestamp")
        if action_f:
            qs = qs.filter(action=action_f)
        if user_f:
            qs = qs.filter(
                Q(user__username__icontains=user_f)
                | Q(user__first_name__icontains=user_f)
                | Q(user__last_name__icontains=user_f)
            )
        action_choices = AuditLog.Action.choices

    if date_from_f:
        try:
            qs = qs.filter(timestamp__date__gte=date_from_f)
        except (ValueError, TypeError):
            pass
    if date_to_f:
        try:
            qs = qs.filter(timestamp__date__lte=date_to_f)
        except (ValueError, TypeError):
            pass

    paginator = Paginator(qs, 50)
    page_obj  = paginator.get_page(request.GET.get("page"))

    return render(request, "tickets/audit_log.html", {
        "page_obj":           page_obj,
        "logs":               page_obj,
        "log_type":           log_type,
        "action_choices":     action_choices,
        "total_ticket_logs":  AuditLog.objects.count(),
        "total_system_logs":  SystemAuditLog.objects.count(),
        "filters": {
            "action":     action_f,
            "user":       user_f,
            "date_from":  date_from_f,
            "date_to":    date_to_f,
        },
    })


# ---------------------------------------------------------------------------
# Gestión de reglas de enrutamiento automático
# ---------------------------------------------------------------------------

@it_required
def routing_rule_list(request):
    _active = [Ticket.Status.ABIERTO, Ticket.Status.EN_PROCESO, Ticket.Status.REABIERTO]
    workload = dict(
        Ticket.objects
        .filter(assigned_to__isnull=False, status__in=_active)
        .values("assigned_to_id")
        .annotate(n=Count("id"))
        .values_list("assigned_to_id", "n")
    )
    rules = list(
        TicketRoutingRule.objects
        .select_related("assigned_to")
        .order_by("category", "area")
    )
    for rule in rules:
        rule.open_tickets = workload.get(rule.assigned_to_id, 0)
    return render(request, "tickets/routing_rules/rule_list.html", {"rules": rules})


@it_required
def routing_rule_create(request):
    form = RoutingRuleForm(request.POST or None)
    if form.is_valid():
        form.save()
        messages.success(request, "Regla de enrutamiento creada.")
        logger.info("Regla de enrutamiento creada por '%s'", request.user.username)
        return redirect("routing_rule_list")
    return render(request, "tickets/routing_rules/rule_form.html", {
        "form":  form,
        "title": "Nueva regla de enrutamiento",
        "btn":   "Crear regla",
    })


@it_required
def routing_rule_edit(request, pk):
    rule = get_object_or_404(TicketRoutingRule, pk=pk)
    form = RoutingRuleForm(request.POST or None, instance=rule)
    if form.is_valid():
        form.save()
        messages.success(request, "Regla actualizada correctamente.")
        logger.info("Regla de enrutamiento #%s editada por '%s'", pk, request.user.username)
        return redirect("routing_rule_list")
    return render(request, "tickets/routing_rules/rule_form.html", {
        "form":  form,
        "rule":  rule,
        "title": f"Editar regla: {rule.get_category_display()}",
        "btn":   "Guardar cambios",
    })


@it_required
def routing_rule_toggle(request, pk):
    if request.method != "POST":
        return redirect("routing_rule_list")
    rule = get_object_or_404(TicketRoutingRule, pk=pk)
    rule.is_active = not rule.is_active
    rule.save(update_fields=["is_active"])
    state = "activada" if rule.is_active else "desactivada"
    messages.success(request, f"Regla {state}.")
    return redirect("routing_rule_list")


@it_required
def routing_rule_delete(request, pk):
    rule = get_object_or_404(TicketRoutingRule, pk=pk)
    if request.method == "POST":
        info = f"{rule.get_category_display()} / {rule.area or 'todas las áreas'}"
        rule.delete()
        messages.success(request, f"Regla eliminada: {info}.")
        logger.info("Regla de enrutamiento #%s eliminada por '%s'", pk, request.user.username)
        return redirect("routing_rule_list")
    return render(request, "tickets/routing_rules/rule_confirm_delete.html", {"rule": rule})


# ---------------------------------------------------------------------------
# Gestión de categorías de ticket
# ---------------------------------------------------------------------------

@it_required
def category_list(request):
    categories = TicketCategory.objects.all()
    return render(request, "tickets/categories/category_list.html", {"categories": categories})


@it_required
def category_create(request):
    form = TicketCategoryForm(request.POST or None)
    if form.is_valid():
        cat = form.save()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.CATEGORY_CREATED,
            user=request.user,
            details=f"Categoría: {cat.name} (código: {cat.code})",
        )
        messages.success(request, f"Categoría «{cat.name}» creada.")
        logger.info("Categoría '%s' creada por '%s'", cat.code, request.user.username)
        return redirect("category_list")
    return render(request, "tickets/categories/category_form.html", {"form": form, "title": "Nueva categoría"})


@it_required
def category_edit(request, pk):
    cat  = get_object_or_404(TicketCategory, pk=pk)
    form = TicketCategoryForm(request.POST or None, instance=cat)
    if form.is_valid():
        form.save()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.CATEGORY_UPDATED,
            user=request.user,
            details=f"Categoría: {cat.name} (código: {cat.code})",
        )
        messages.success(request, f"Categoría «{cat.name}» actualizada.")
        logger.info("Categoría '%s' editada por '%s'", cat.code, request.user.username)
        return redirect("category_list")
    return render(request, "tickets/categories/category_form.html", {
        "form":  form,
        "title": f"Editar categoría: {cat.name}",
        "cat":   cat,
    })


@it_required
def category_toggle(request, pk):
    if request.method != "POST":
        return redirect("category_list")
    cat = get_object_or_404(TicketCategory, pk=pk)
    cat.is_active = not cat.is_active
    cat.save(update_fields=["is_active"])
    state = "activada" if cat.is_active else "desactivada"
    messages.success(request, f"Categoría «{cat.name}» {state}.")
    return redirect("category_list")


# ---------------------------------------------------------------------------
# Gestión de políticas SLA
# ---------------------------------------------------------------------------

@it_required
def sla_policy_list(request):
    """Lista las 4 políticas SLA (una por nivel de prioridad)."""
    policies = SLAPolicy.objects.order_by("priority")
    priority_order = {v: i for i, (v, _) in enumerate(Ticket.Priority.choices)}
    policies = sorted(policies, key=lambda p: priority_order.get(p.priority, 99))
    return render(request, "tickets/sla/sla_list.html", {
        "policies":  policies,
        "priorities": Ticket.Priority.choices,
    })


@permission_required("tickets.gestionar")
def sla_policy_edit(request, pk):
    """Edita las horas de resolución de una política SLA."""
    policy = get_object_or_404(SLAPolicy, pk=pk)
    form   = SLAPolicyForm(request.POST or None, instance=policy)
    if form.is_valid():
        form.save()
        SystemAuditLog.objects.create(
            action=SystemAuditLog.Action.SLA_UPDATED,
            user=request.user,
            details=(
                f"SLA [{policy.get_priority_display()}] actualizado: "
                f"{policy.resolution_hours}h"
            ),
        )
        messages.success(
            request,
            f"SLA para prioridad {policy.get_priority_display()} "
            f"actualizado a {policy.resolution_hours}h.",
        )
        logger.info(
            "SLA [%s] → %sh editado por '%s'",
            policy.priority, policy.resolution_hours, request.user.username,
        )
        return redirect("sla_policy_list")
    return render(request, "tickets/sla/sla_form.html", {
        "form":   form,
        "policy": policy,
        "title":  f"Editar SLA — {policy.get_priority_display()}",
    })
