import os

from django import forms
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password

from .models import Role, RolePermission, SLAPolicy, Ticket, TicketAttachment, TicketCategory, TicketNote, TicketRoutingRule

_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf", ".docx", ".xlsx", ".zip", ".txt", ".log"}
_MAX_UPLOAD_BYTES   = 10 * 1024 * 1024  # 10 MB

_INPUT_STYLE = "font-family:'Montserrat',sans-serif;width:100%;border:1px solid #9A979F;border-radius:10px;padding:14px 16px;font-size:15px;box-sizing:border-box;background:#FFFFFF;color:#000000;outline:none;"


class TicketCreateForm(forms.ModelForm):
    """Formulario para usuarios OMA/OPERADOR al crear un ticket."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["category"] = forms.ChoiceField(
            choices=TicketCategory.active_choices(),
            widget=forms.Select(attrs={"style": _INPUT_STYLE}),
            label="Tipo de problema",
        )

    class Meta:
        model  = Ticket
        fields = ["category", "priority", "title", "description"]
        widgets = {
            "title": forms.TextInput(attrs={
                "placeholder": "Ej: No puedo acceder al correo corporativo",
                "style": _INPUT_STYLE,
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Describe el problema con el mayor detalle posible: desde cuándo ocurre, qué equipo, qué mensaje de error ves.",
                "style": _INPUT_STYLE + "min-height:160px;resize:vertical;",
            }),
            "priority": forms.Select(attrs={"style": _INPUT_STYLE}),
        }
        labels = {
            "title":       "Título del problema",
            "description": "Descripción detallada",
            "priority":    "Prioridad",
        }


class TicketResponseForm(forms.ModelForm):
    """Formulario para técnicos IT al gestionar un ticket."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = (
            User.objects.filter(profile__role__is_it_staff=True).select_related("profile")
        )
        self.fields["assigned_to"].empty_label = "Sin asignar"

        choices = TicketCategory.active_choices()
        # If the ticket already has an inactive category, keep it visible so the
        # technician sees the real category and doesn't accidentally change it.
        if self.instance and self.instance.pk and self.instance.category:
            active_codes = {code for code, _ in choices}
            if self.instance.category not in active_codes:
                try:
                    cat = TicketCategory.objects.get(code=self.instance.category)
                    choices = [(cat.code, f"{cat.name} (inactiva)")] + choices
                except TicketCategory.DoesNotExist:
                    choices = [(self.instance.category, self.instance.category)] + choices

        self.fields["category"] = forms.ChoiceField(
            choices=choices,
            widget=forms.Select(attrs={"style": _INPUT_STYLE}),
            label="Categoría",
        )

    class Meta:
        model  = Ticket
        fields = ["response", "status", "priority", "category", "assigned_to"]
        widgets = {
            "response": forms.Textarea(attrs={
                "placeholder": "Escriba aquí la gestión realizada, pasos seguidos y solución aplicada.",
                "style": _INPUT_STYLE + "min-height:160px;resize:vertical;",
            }),
            "status":      forms.Select(attrs={"style": _INPUT_STYLE}),
            "priority":    forms.Select(attrs={"style": _INPUT_STYLE}),
            "category":    forms.Select(attrs={"style": _INPUT_STYLE}),
            "assigned_to": forms.Select(attrs={"style": _INPUT_STYLE}),
        }
        labels = {
            "response":    "Respuesta / Gestión de IT",
            "status":      "Estado del ticket",
            "priority":    "Prioridad",
            "category":    "Categoría",
            "assigned_to": "Técnico asignado",
        }


class TicketNoteForm(forms.ModelForm):
    """Nota de IT para un ticket (interna o visible al usuario)."""

    class Meta:
        model  = TicketNote
        fields = ["content", "is_public"]
        widgets = {
            "content": forms.Textarea(attrs={
                "placeholder": "Nota interna — solo visible para el equipo IT.",
                "style": _INPUT_STYLE + "min-height:80px;resize:vertical;",
                "rows": 3,
            }),
            "is_public": forms.CheckboxInput(),
        }
        labels = {
            "content":   "Nota",
            "is_public": "Visible al solicitante",
        }


class UserCommentForm(forms.ModelForm):
    """Comentario público del usuario en su propio ticket."""

    class Meta:
        model  = TicketNote
        fields = ["content"]
        widgets = {
            "content": forms.Textarea(attrs={
                "placeholder": "Escribe un comentario o información adicional sobre tu solicitud.",
                "style": _INPUT_STYLE + "min-height:80px;resize:vertical;",
                "rows": 3,
            }),
        }
        labels = {"content": "Tu comentario"}


class AttachmentUploadForm(forms.Form):
    """Validación de archivo adjunto: extensión y tamaño."""
    file = forms.FileField(label="Archivo adjunto")

    def clean_file(self):
        f   = self.cleaned_data["file"]
        ext = os.path.splitext(f.name)[1].lower()
        if ext not in _ALLOWED_EXTENSIONS:
            allowed = ", ".join(sorted(_ALLOWED_EXTENSIONS))
            raise forms.ValidationError(f"Tipo no permitido. Permitidos: {allowed}")
        if f.size > _MAX_UPLOAD_BYTES:
            raise forms.ValidationError("El archivo supera el límite de 10 MB.")
        return f


class UserCreateForm(forms.Form):
    """Crea un nuevo usuario Django + UserProfile en un solo formulario."""

    username   = forms.CharField(
        max_length=150, label="Nombre de usuario",
        widget=forms.TextInput(attrs={"placeholder": "Ej: jperez", "style": _INPUT_STYLE}),
    )
    first_name = forms.CharField(
        max_length=150, label="Nombre",
        widget=forms.TextInput(attrs={"placeholder": "Juan", "style": _INPUT_STYLE}),
    )
    last_name  = forms.CharField(
        max_length=150, label="Apellido",
        widget=forms.TextInput(attrs={"placeholder": "Pérez", "style": _INPUT_STYLE}),
    )
    email      = forms.EmailField(
        label="Correo corporativo",
        widget=forms.EmailInput(attrs={"placeholder": "jperez@lascargo.com", "style": _INPUT_STYLE}),
    )
    department = forms.CharField(
        max_length=100, required=False, label="Departamento",
        widget=forms.TextInput(attrs={"placeholder": "Ej: Operaciones", "style": _INPUT_STYLE}),
    )
    role       = forms.ModelChoiceField(
        queryset=Role.objects.filter(is_active=True).order_by("name"),
        label="Rol",
        widget=forms.Select(attrs={"style": _INPUT_STYLE}),
    )
    password1  = forms.CharField(
        label="Contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Mínimo 8 caracteres", "style": _INPUT_STYLE}),
    )
    password2  = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Repite la contraseña", "style": _INPUT_STYLE}),
    )

    def clean_username(self):
        username = self.cleaned_data["username"]
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Este nombre de usuario ya está registrado.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"]
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Este correo ya está registrado en el sistema.")
        return email

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1")
        p2 = cleaned.get("password2")
        if p1 and p2 and p1 != p2:
            self.add_error("password2", "Las contraseñas no coinciden.")
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("password1", exc)
        return cleaned


class UserEditForm(forms.Form):
    """Edita datos de perfil de un usuario existente."""

    first_name = forms.CharField(
        max_length=150, label="Nombre",
        widget=forms.TextInput(attrs={"style": _INPUT_STYLE}),
    )
    last_name  = forms.CharField(
        max_length=150, label="Apellido",
        widget=forms.TextInput(attrs={"style": _INPUT_STYLE}),
    )
    email      = forms.EmailField(
        label="Correo corporativo",
        widget=forms.EmailInput(attrs={"style": _INPUT_STYLE}),
    )
    department = forms.CharField(
        max_length=100, required=False, label="Departamento",
        widget=forms.TextInput(attrs={"style": _INPUT_STYLE}),
    )
    role       = forms.ModelChoiceField(
        queryset=Role.objects.filter(is_active=True).order_by("name"),
        label="Rol", required=True,
        widget=forms.Select(attrs={"style": _INPUT_STYLE}),
    )

    def __init__(self, *args, user_pk=None, **kwargs):
        self._user_pk = user_pk
        super().__init__(*args, **kwargs)

    def clean_email(self):
        email = self.cleaned_data["email"]
        qs = User.objects.filter(email=email)
        if self._user_pk:
            qs = qs.exclude(pk=self._user_pk)
        if qs.exists():
            raise forms.ValidationError("Este correo ya está registrado por otro usuario.")
        return email


class SetPasswordAdminForm(forms.Form):
    """Permite al administrador cambiar la contraseña de cualquier usuario."""

    new_password1 = forms.CharField(
        label="Nueva contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Mínimo 8 caracteres", "style": _INPUT_STYLE}),
    )
    new_password2 = forms.CharField(
        label="Confirmar contraseña",
        widget=forms.PasswordInput(attrs={"placeholder": "Repite la nueva contraseña", "style": _INPUT_STYLE}),
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("new_password1")
        p2 = cleaned.get("new_password2")
        if p1 and p2 and p1 != p2:
            self.add_error("new_password2", "Las contraseñas no coinciden.")
        elif p1:
            try:
                validate_password(p1)
            except forms.ValidationError as exc:
                self.add_error("new_password1", exc)
        return cleaned


class RoutingRuleForm(forms.ModelForm):
    """Crea o edita una regla de enrutamiento automático de tickets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assigned_to"].queryset = (
            User.objects.filter(profile__role__is_it_staff=True)
            .select_related("profile__role")
            .order_by("first_name", "username")
        )
        self.fields["assigned_to"].label_from_instance = (
            lambda u: u.get_full_name() or u.username
        )

        choices = TicketCategory.active_choices()
        if self.instance and self.instance.pk and self.instance.category:
            active_codes = {code for code, _ in choices}
            if self.instance.category not in active_codes:
                try:
                    cat = TicketCategory.objects.get(code=self.instance.category)
                    choices = [(cat.code, f"{cat.name} (inactiva)")] + choices
                except TicketCategory.DoesNotExist:
                    choices = [(self.instance.category, self.instance.category)] + choices

        self.fields["category"] = forms.ChoiceField(
            choices=choices,
            widget=forms.Select(attrs={"style": _INPUT_STYLE}),
            label="Categoría de ticket",
        )

    class Meta:
        model  = TicketRoutingRule
        fields = ["category", "area", "assigned_to", "is_active"]
        widgets = {
            "category":    forms.Select(attrs={"style": _INPUT_STYLE}),
            "area":        forms.Select(attrs={"style": _INPUT_STYLE}),
            "assigned_to": forms.Select(attrs={"style": _INPUT_STYLE}),
        }
        labels = {
            "category":    "Categoría de ticket",
            "area":        "Área (dejar vacío = todas las áreas)",
            "assigned_to": "Técnico asignado",
            "is_active":   "Regla activa",
        }


class TicketCategoryForm(forms.ModelForm):
    """Crea o edita una categoría de ticket."""

    class Meta:
        model  = TicketCategory
        fields = ["code", "name", "description", "is_active", "order"]
        widgets = {
            "code": forms.TextInput(attrs={
                "placeholder": "Ej: REDES (sin espacios, mayúsculas)",
                "style": _INPUT_STYLE,
            }),
            "name": forms.TextInput(attrs={
                "placeholder": "Ej: Redes y conectividad",
                "style": _INPUT_STYLE,
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Descripción opcional de la categoría.",
                "style": _INPUT_STYLE + "min-height:80px;resize:vertical;",
                "rows": 3,
            }),
            "order": forms.NumberInput(attrs={"style": _INPUT_STYLE}),
        }
        labels = {
            "code":        "Código único",
            "name":        "Nombre visible",
            "description": "Descripción",
            "is_active":   "Activa (aparece en formularios)",
            "order":       "Orden (menor = primero en la lista)",
        }

    def clean_code(self):
        code = self.cleaned_data["code"].upper().strip()
        qs = TicketCategory.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Este código ya está en uso.")
        return code


class SLAPolicyForm(forms.ModelForm):
    """Edita las horas de resolución de una política SLA."""

    class Meta:
        model  = SLAPolicy
        fields = ["resolution_hours"]
        widgets = {
            "resolution_hours": forms.NumberInput(attrs={
                "min": 1, "max": 720,
                "style": _INPUT_STYLE,
            }),
        }
        labels = {"resolution_hours": "Horas máximas de resolución"}

    def clean_resolution_hours(self):
        hours = self.cleaned_data["resolution_hours"]
        if hours < 1:
            raise forms.ValidationError("El número de horas debe ser al menos 1.")
        if hours > 720:
            raise forms.ValidationError("El número de horas no puede superar 720 (30 días).")
        return hours


class RoleForm(forms.ModelForm):
    """Formulario para crear y editar roles dinámicos."""

    permissions = forms.ModelMultipleChoiceField(
        queryset=RolePermission.objects.order_by("module", "action"),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Permisos",
    )

    class Meta:
        model  = Role
        fields = ["name", "code", "description", "is_active", "is_it_staff", "ticket_area", "permissions"]
        widgets = {
            "name": forms.TextInput(attrs={
                "placeholder": "Ej: Supervisor, Analista IT",
                "style": _INPUT_STYLE,
            }),
            "code": forms.TextInput(attrs={
                "placeholder": "Ej: SUPERVISOR (sin espacios)",
                "style": _INPUT_STYLE,
            }),
            "description": forms.Textarea(attrs={
                "placeholder": "Descripción opcional del rol y sus responsabilidades.",
                "style": _INPUT_STYLE + "min-height:80px;resize:vertical;",
                "rows": 3,
            }),
            "ticket_area": forms.Select(attrs={"style": _INPUT_STYLE}),
        }
        labels = {
            "name":        "Nombre del rol",
            "code":        "Código único",
            "description": "Descripción",
            "is_active":   "Rol activo",
            "is_it_staff": "Personal IT (accede al panel IT)",
            "ticket_area": "Área de tickets",
        }
