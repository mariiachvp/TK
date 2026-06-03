import os
import uuid

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone


class RolePermission(models.Model):
    """Permiso individual por módulo y acción, asignable a roles dinámicos."""

    class Module(models.TextChoices):
        TICKETS  = "tickets",  "Tickets"
        REPORTES = "reportes", "Reportes"
        USUARIOS = "usuarios", "Usuarios"
        ROLES    = "roles",    "Gestión de roles"

    class Action(models.TextChoices):
        VER       = "ver",       "Ver"
        CREAR     = "crear",     "Crear"
        EDITAR    = "editar",    "Editar"
        ELIMINAR  = "eliminar",  "Eliminar"
        RESPONDER = "responder", "Responder tickets"
        ASIGNAR   = "asignar",   "Asignar técnico"
        EXPORTAR  = "exportar",  "Exportar datos"
        GESTIONAR = "gestionar", "Gestionar"

    module      = models.CharField(max_length=30, choices=Module.choices)
    action      = models.CharField(max_length=30, choices=Action.choices)
    codename    = models.SlugField(max_length=80, unique=True)
    label       = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    class Meta:
        unique_together     = [("module", "action")]
        ordering            = ["module", "action"]
        verbose_name        = "Permiso"
        verbose_name_plural = "Permisos"

    def __str__(self):
        return f"{self.label} [{self.codename}]"


class Role(models.Model):
    """Rol dinámico con permisos granulares administrables desde la UI."""

    name        = models.CharField(max_length=80, unique=True, verbose_name="Nombre")
    code        = models.SlugField(
        max_length=50, unique=True, verbose_name="Código",
        help_text="Identificador único. Solo letras, números y guiones. Ej: IT, OMA, SUPERVISOR."
    )
    description = models.TextField(blank=True, default="", verbose_name="Descripción")
    is_active   = models.BooleanField(default=True, verbose_name="Activo")
    is_it_staff = models.BooleanField(
        default=False, verbose_name="Personal IT",
        help_text="Permite acceder al panel IT: responder tickets, dashboards, estadísticas."
    )
    ticket_area = models.CharField(
        max_length=20,
        choices=[("OMA", "OMA"), ("OPERADOR", "Operador"), ("", "N/A — Personal IT")],
        blank=True, default="",
        verbose_name="Área de tickets",
        help_text="Área para tickets creados por usuarios con este rol. Vacío para roles IT."
    )
    permissions = models.ManyToManyField(
        RolePermission, blank=True, related_name="roles", verbose_name="Permisos"
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering            = ["name"]
        verbose_name        = "Rol"
        verbose_name_plural = "Roles"

    def __str__(self):
        return self.name + ("" if self.is_active else " (inactivo)")

    def has_permission(self, codename: str) -> bool:
        if not self.is_active:
            return False
        return self.permissions.filter(codename=codename).exists()

    @property
    def permission_count(self):
        return self.permissions.count()

    @property
    def user_count(self):
        return self.users.count()


class UserProfile(models.Model):
    user       = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    role       = models.ForeignKey(
        Role, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="users",
        verbose_name="Rol"
    )
    department = models.CharField(
        max_length=100, blank=True, default="", verbose_name="Departamento"
    )

    def __str__(self):
        role_name = self.role.name if self.role else "Sin rol"
        return f"{self.user.username} — {role_name}"

    @property
    def is_it_staff(self) -> bool:
        return bool(self.role and self.role.is_active and self.role.is_it_staff)

    def has_permission(self, codename: str) -> bool:
        if not self.role:
            return False
        return self.role.has_permission(codename)

    @property
    def can_manage_users(self) -> bool:
        return bool(self.role and self.role.is_active and self.has_permission("usuarios.gestionar"))


class TicketCategory(models.Model):
    """Categorías de ticket administrables desde el panel IT."""

    code        = models.SlugField(max_length=20, unique=True, verbose_name="Código")
    name        = models.CharField(max_length=100, verbose_name="Nombre")
    description = models.TextField(blank=True, default="", verbose_name="Descripción")
    is_active   = models.BooleanField(default=True, verbose_name="Activa")
    order       = models.PositiveSmallIntegerField(default=0, verbose_name="Orden")

    _cache: dict = {}

    class Meta:
        ordering            = ["order", "name"]
        verbose_name        = "Categoría"
        verbose_name_plural = "Categorías"

    def __str__(self):
        return self.name + ("" if self.is_active else " (inactiva)")

    def save(self, *args, **kwargs):
        TicketCategory._cache.clear()
        super().save(*args, **kwargs)

    @classmethod
    def get_label(cls, code: str) -> str:
        if not cls._cache:
            cls._cache = dict(cls.objects.values_list("code", "name"))
        return cls._cache.get(code, code)

    @classmethod
    def active_choices(cls):
        return list(
            cls.objects.filter(is_active=True).order_by("order", "name").values_list("code", "name")
        )


class ActiveTicketManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Ticket(models.Model):
    class Area(models.TextChoices):
        OMA      = "OMA",      "OMA"
        OPERADOR = "OPERADOR", "Operador"

    class Status(models.TextChoices):
        ABIERTO    = "ABIERTO",    "Abierto"
        EN_PROCESO = "EN_PROCESO", "En progreso"
        RESUELTO   = "RESUELTO",   "Resuelto"
        CERRADO    = "CERRADO",    "Cerrado"
        REABIERTO  = "REABIERTO",  "Reabierto"

    class Priority(models.TextChoices):
        LOW      = "LOW",      "Baja"
        MEDIUM   = "MEDIUM",   "Media"
        HIGH     = "HIGH",     "Alta"
        CRITICAL = "CRITICAL", "Crítica"

    class Category(models.TextChoices):
        MICROSOFT_365 = "M365",        "Microsoft 365"
        OUTLOOK       = "OUTLOOK",     "Outlook / Correo"
        REDES         = "REDES",       "Redes"
        EQUIPOS       = "EQUIPOS",     "Equipos / Hardware"
        SEGURIDAD     = "SEGURIDAD",   "Seguridad"
        VPN           = "VPN",         "VPN / Acceso Remoto"
        SHAREPOINT    = "SHAREPOINT",  "SharePoint"
        AZURE         = "AZURE",       "Azure"
        APPS          = "APPS",        "Aplicaciones Corporativas"
        OTRO          = "OTRO",        "Otro"

    # --- Campos originales ---
    ticket_number = models.CharField(max_length=20, blank=True, default="", db_index=True)
    requester   = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tickets")
    area        = models.CharField(max_length=20, choices=Area.choices)
    title       = models.CharField(max_length=200)
    description = models.TextField()
    response    = models.TextField(blank=True, default="")
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.ABIERTO)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)
    is_deleted  = models.BooleanField(default=False)
    deleted_at  = models.DateTimeField(null=True, blank=True)
    deleted_by  = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="deleted_tickets"
    )

    # --- Campos Fase 2 ---
    priority    = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    category    = models.CharField(max_length=20, default=Category.OTRO)
    assigned_to = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_tickets"
    )
    started_at   = models.DateTimeField(null=True, blank=True)
    resolved_at  = models.DateTimeField(null=True, blank=True)
    sla_deadline      = models.DateTimeField(null=True, blank=True)
    sla_met           = models.BooleanField(null=True)
    sla_warning_sent  = models.BooleanField(default=False)

    objects     = ActiveTicketManager()
    all_objects = models.Manager()

    class Meta:
        ordering             = ["-created_at"]
        base_manager_name    = "all_objects"
        default_manager_name = "objects"

    def __str__(self):
        num = self.ticket_number or f"#{self.pk}"
        return f"{num} {self.title} [{self.area}]"

    @property
    def responded(self):
        return bool(self.response and self.response.strip())

    @property
    def resolution_time_minutes(self):
        if self.started_at and self.resolved_at:
            return max(0, int((self.resolved_at - self.started_at).total_seconds() / 60))
        return None

    @property
    def is_overdue(self):
        active = {self.Status.ABIERTO, self.Status.EN_PROCESO, self.Status.REABIERTO}
        if self.sla_deadline and self.status in active:
            return timezone.now() > self.sla_deadline
        return False

    def get_category_display(self) -> str:
        return TicketCategory.get_label(self.category) if self.category else "—"

    @property
    def priority_css_class(self):
        return {
            self.Priority.LOW:      "priority-low",
            self.Priority.MEDIUM:   "priority-medium",
            self.Priority.HIGH:     "priority-high",
            self.Priority.CRITICAL: "priority-critical",
        }.get(self.priority, "priority-medium")


class SLAPolicy(models.Model):
    priority         = models.CharField(
        max_length=10, choices=Ticket.Priority.choices, unique=True
    )
    resolution_hours = models.PositiveIntegerField(
        help_text="Horas máximas para resolver el ticket desde su creación"
    )

    class Meta:
        verbose_name        = "Política SLA"
        verbose_name_plural = "Políticas SLA"
        ordering            = ["priority"]

    def __str__(self):
        return f"SLA [{self.priority}]: {self.resolution_hours}h"


class TicketWorkSession(models.Model):
    ticket     = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="work_sessions")
    technician = models.ForeignKey(User, on_delete=models.PROTECT, related_name="work_sessions")
    started_at = models.DateTimeField()
    ended_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering            = ["-started_at"]
        verbose_name        = "Sesión de trabajo"
        verbose_name_plural = "Sesiones de trabajo"

    def __str__(self):
        return f"Ticket #{self.ticket_id} — {self.technician.username} ({self.started_at:%d/%m/%Y %H:%M})"

    @property
    def duration_minutes(self):
        end = self.ended_at or timezone.now()
        return max(0, int((end - self.started_at).total_seconds() / 60))


class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATED       = "CREATED",       "Creado"
        STATUS_CHANGE = "STATUS_CHANGE", "Cambio de estado"
        RESPONDED     = "RESPONDED",     "Respuesta registrada"
        FIELD_CHANGE  = "FIELD_CHANGE",  "Campo modificado"
        ESCALATED     = "ESCALATED",     "Escalado automáticamente"
        REOPENED           = "REOPENED",           "Reabierto por usuario"
        ASSIGNED           = "ASSIGNED",           "Técnico asignado"
        REASSIGNED         = "REASSIGNED",         "Técnico reasignado"
        COMMENT_ADDED      = "COMMENT_ADDED",      "Comentario agregado"
        ATTACHMENT_ADDED   = "ATTACHMENT_ADDED",   "Adjunto agregado"
        ATTACHMENT_DELETED = "ATTACHMENT_DELETED", "Adjunto eliminado"

    ticket    = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="audit_logs")
    user      = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action    = models.CharField(max_length=30, choices=Action.choices)
    field     = models.CharField(max_length=50, blank=True, default="")
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ["-timestamp"]
        verbose_name        = "Registro de auditoría"
        verbose_name_plural = "Registros de auditoría"

    def __str__(self):
        return f"Ticket #{self.ticket_id} | {self.action} | {self.timestamp:%d/%m/%Y %H:%M}"


class TicketRoutingRule(models.Model):
    category    = models.CharField(max_length=20)
    area        = models.CharField(
        max_length=20, choices=Ticket.Area.choices, blank=True, default="",
        help_text="Dejar vacío para que aplique a todas las áreas.",
    )
    assigned_to = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="routing_rules",
        limit_choices_to={"profile__role__is_it_staff": True},
    )
    is_active   = models.BooleanField(default=True)

    class Meta:
        verbose_name        = "Regla de enrutamiento"
        verbose_name_plural = "Reglas de enrutamiento"
        ordering            = ["category", "area"]
        unique_together     = [("category", "area")]

    def get_category_display(self) -> str:
        return TicketCategory.get_label(self.category) if self.category else "—"

    def __str__(self):
        area_label = self.area or "todas las áreas"
        return f"{self.get_category_display()} / {area_label} → {self.assigned_to.username}"


class TicketNote(models.Model):
    ticket     = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="notes")
    author     = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="ticket_notes"
    )
    content    = models.TextField()
    is_public  = models.BooleanField(default=False, verbose_name="Visible al solicitante")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ["created_at"]
        verbose_name        = "Nota / Comentario"
        verbose_name_plural = "Notas / Comentarios"

    def __str__(self):
        return f"Nota #{self.pk} — Ticket #{self.ticket_id}"


def _attachment_upload_path(instance, filename):
    ext = os.path.splitext(filename)[1].lower()
    return f"attachments/ticket_{instance.ticket_id}/{uuid.uuid4().hex}{ext}"


_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx", ".zip", ".txt", ".log"}


class TicketAttachment(models.Model):
    ticket        = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="attachments")
    uploaded_by   = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="uploaded_attachments"
    )
    file          = models.FileField(upload_to=_attachment_upload_path)
    original_name = models.CharField(max_length=255)
    file_size     = models.PositiveIntegerField()
    content_type  = models.CharField(max_length=100, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ["created_at"]
        verbose_name        = "Adjunto"
        verbose_name_plural = "Adjuntos"

    def __str__(self):
        return f"{self.original_name} (Ticket #{self.ticket_id})"

    @property
    def extension(self):
        return os.path.splitext(self.original_name)[1].lower()

    @property
    def size_display(self):
        if self.file_size < 1024:
            return f"{self.file_size} B"
        if self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        return f"{self.file_size / (1024 * 1024):.1f} MB"

    @property
    def is_image(self):
        return self.extension in {".jpg", ".jpeg", ".png"}


class SystemAuditLog(models.Model):
    """Registro inmutable de eventos de sistema: login, logout, gestión de usuarios."""

    class Action(models.TextChoices):
        LOGIN            = "LOGIN",            "Inicio de sesión"
        LOGOUT           = "LOGOUT",           "Cierre de sesión"
        LOGIN_FAILED     = "LOGIN_FAILED",     "Intento de login fallido"
        USER_CREATED     = "USER_CREATED",     "Usuario creado"
        USER_UPDATED     = "USER_UPDATED",     "Usuario actualizado"
        USER_DEACTIVATED = "USER_DEACTIVATED", "Usuario desactivado"
        USER_ACTIVATED   = "USER_ACTIVATED",   "Usuario activado"
        PASSWORD_CHANGED  = "PASSWORD_CHANGED",  "Contraseña cambiada"
        NOTIFICATION_SENT = "NOTIFICATION_SENT", "Notificación enviada"

    action      = models.CharField(max_length=20, choices=Action.choices)
    user        = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="system_audit_actions",
        verbose_name="Actor"
    )
    target_user = models.ForeignKey(
        User, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="system_audit_targets",
        verbose_name="Usuario afectado"
    )
    ip_address  = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP")
    details     = models.TextField(blank=True, default="", verbose_name="Detalles")
    timestamp   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering            = ["-timestamp"]
        verbose_name        = "Auditoría del sistema"
        verbose_name_plural = "Auditorías del sistema"

    def __str__(self):
        actor  = self.user.username if self.user else "Sistema"
        target = f" → {self.target_user.username}" if self.target_user else ""
        return f"{self.get_action_display()} | {actor}{target} | {self.timestamp:%d/%m/%Y %H:%M}"


class TicketCounter(models.Model):
    """Contador atómico por año — garantiza numeración única y secuencial de tickets."""
    year    = models.PositiveIntegerField(unique=True)
    counter = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Contador de tickets"

    def __str__(self):
        return f"TK-{self.year}: {self.counter}"
