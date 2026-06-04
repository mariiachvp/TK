from django.contrib import admin
from django.utils.html import format_html

from .models import (
    AuditLog, Role, RolePermission, SLAPolicy, SystemAuditLog, Ticket,
    TicketAttachment, TicketCategory, TicketNote, TicketRoutingRule, TicketWorkSession, UserProfile,
)


# ---------------------------------------------------------------------------
# Roles y permisos
# ---------------------------------------------------------------------------

@admin.register(RolePermission)
class RolePermissionAdmin(admin.ModelAdmin):
    list_display  = ("label", "codename", "module", "action")
    list_filter   = ("module",)
    search_fields = ("label", "codename")
    ordering      = ("module", "action")

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser


class RolePermissionInline(admin.TabularInline):
    model              = Role.permissions.through
    extra              = 0
    verbose_name       = "Permiso asignado"
    verbose_name_plural = "Permisos asignados"
    autocomplete_fields = ["rolepermission"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display   = ("name", "code", "is_active_badge", "is_it_staff", "ticket_area",
                      "permission_count_display", "user_count_display", "updated_at")
    list_filter    = ("is_active", "is_it_staff", "ticket_area")
    search_fields  = ("name", "code", "description")
    filter_horizontal = ("permissions",)
    readonly_fields = ("created_at", "updated_at", "permission_count_display", "user_count_display")
    fieldsets = (
        ("Identificación", {
            "fields": ("name", "code", "description"),
        }),
        ("Acceso", {
            "fields": ("is_active", "is_it_staff", "ticket_area"),
        }),
        ("Permisos", {
            "fields": ("permissions",),
            "classes": ("wide",),
        }),
        ("Metadatos", {
            "fields": ("permission_count_display", "user_count_display", "created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )
    actions = ["activate_roles", "deactivate_roles"]

    @admin.display(description="Activo", boolean=False)
    def is_active_badge(self, obj):
        if obj.is_active:
            return format_html('<span style="color:#198754;font-weight:600;">{}</span>', '✓ Activo')
        return format_html('<span style="color:#dc3545;font-weight:600;">{}</span>', '✗ Inactivo')

    @admin.display(description="Permisos")
    def permission_count_display(self, obj):
        return obj.permission_count

    @admin.display(description="Usuarios")
    def user_count_display(self, obj):
        return obj.user_count

    @admin.action(description="Activar roles seleccionados")
    def activate_roles(self, request, queryset):
        updated = queryset.update(is_active=True)
        self.message_user(request, f"{updated} rol(es) activado(s).")

    @admin.action(description="Desactivar roles seleccionados")
    def deactivate_roles(self, request, queryset):
        updated = queryset.update(is_active=False)
        self.message_user(request, f"{updated} rol(es) desactivado(s).")


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display  = ("user", "role", "department", "role_is_active", "role_is_it_staff")
    list_filter   = ("role", "role__is_active", "role__is_it_staff")
    search_fields = ("user__username", "user__email", "user__first_name", "user__last_name", "department")
    autocomplete_fields = ["role"]
    fieldsets = (
        (None, {"fields": ("user", "role", "department")}),
    )

    @admin.display(description="Rol activo", boolean=True)
    def role_is_active(self, obj):
        return bool(obj.role and obj.role.is_active)

    @admin.display(description="Personal IT", boolean=True)
    def role_is_it_staff(self, obj):
        return obj.is_it_staff


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display    = ("id", "title", "requester", "area", "priority", "category",
                       "status", "assigned_to", "has_response", "sla_met", "created_at")
    list_filter     = ("area", "status", "priority", "category", "sla_met")
    search_fields   = ("title", "description", "requester__username", "assigned_to__username")
    readonly_fields = ("created_at", "updated_at", "started_at", "resolved_at",
                       "sla_deadline", "sla_met")

    @admin.display(boolean=True, description="Respondido")
    def has_response(self, obj):
        return obj.responded


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display  = ("code", "name", "is_active", "order", "description_preview")
    list_filter   = ("is_active",)
    search_fields = ("code", "name")
    list_editable = ("is_active", "order")
    ordering      = ("order", "name")

    @admin.display(description="Descripción")
    def description_preview(self, obj):
        return obj.description[:60] + ("…" if len(obj.description) > 60 else "")


@admin.register(SLAPolicy)
class SLAPolicyAdmin(admin.ModelAdmin):
    list_display = ("priority", "resolution_hours")


@admin.register(TicketWorkSession)
class TicketWorkSessionAdmin(admin.ModelAdmin):
    list_display    = ("ticket", "technician", "started_at", "ended_at", "duration_display")
    list_filter     = ("technician",)
    readonly_fields = ("started_at",)

    @admin.display(description="Duración")
    def duration_display(self, obj):
        m = obj.duration_minutes
        if m < 60:
            return f"{m}m"
        h, mins = divmod(m, 60)
        return f"{h}h {mins}m" if mins else f"{h}h"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display    = ("ticket", "action", "field", "user", "timestamp")
    list_filter     = ("action",)
    search_fields   = ("ticket__title", "user__username")
    readonly_fields = ("ticket", "user", "action", "field", "old_value", "new_value", "timestamp")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TicketRoutingRule)
class TicketRoutingRuleAdmin(admin.ModelAdmin):
    list_display  = ("category", "area_display", "assigned_to", "is_active")
    list_filter   = ("is_active", "category")
    list_editable = ("is_active",)

    @admin.display(description="Área")
    def area_display(self, obj):
        return obj.area or "Todas"


@admin.register(SystemAuditLog)
class SystemAuditLogAdmin(admin.ModelAdmin):
    list_display    = ("timestamp", "action", "user", "target_user", "ip_address", "details_preview")
    list_filter     = ("action",)
    search_fields   = ("user__username", "target_user__username", "ip_address", "details")
    readonly_fields = ("action", "user", "target_user", "ip_address", "details", "timestamp")
    date_hierarchy  = "timestamp"

    @admin.display(description="Detalles")
    def details_preview(self, obj):
        return obj.details[:80] + ("…" if len(obj.details) > 80 else "")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TicketAttachment)
class TicketAttachmentAdmin(admin.ModelAdmin):
    list_display    = ("ticket", "original_name", "uploaded_by", "size_display_col", "created_at")
    list_filter     = ("created_at",)
    search_fields   = ("ticket__title", "original_name", "uploaded_by__username")
    readonly_fields = ("ticket", "uploaded_by", "file", "original_name", "file_size", "content_type", "created_at")

    @admin.display(description="Tamaño")
    def size_display_col(self, obj):
        return obj.size_display

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TicketNote)
class TicketNoteAdmin(admin.ModelAdmin):
    list_display    = ("ticket", "author", "created_at", "content_preview")
    list_filter     = ("author",)
    search_fields   = ("ticket__title", "content", "author__username")
    readonly_fields = ("ticket", "author", "created_at")

    @admin.display(description="Contenido")
    def content_preview(self, obj):
        return obj.content[:80] + ("…" if len(obj.content) > 80 else "")

    def has_add_permission(self, request):
        return False
