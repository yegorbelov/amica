from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.contenttypes.admin import GenericTabularInline

from apps.media_files.models.models import DisplayPhoto, DisplayVideo

from .forms import CustomUserChangeForm, CustomUserCreationForm
from .models.models import (
    ActiveSession,
    CustomUser,
    Profile,
    DeviceRecoveryCooldown,
    EmailVerificationOtp,
    RecoveryEmailOtp,
    DeviceLoginChallenge,
    UserWebAuthnCredential,
)


class ProfileAvatarInline(GenericTabularInline):
    model = DisplayPhoto
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    max_num = 1
    verbose_name = "Avatar"
    verbose_name_plural = "Avatar"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.instance = obj
        return formset


class ProfileVideoInline(GenericTabularInline):
    model = DisplayVideo
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    verbose_name = "Video"
    verbose_name_plural = "Videos"

    def get_formset(self, request, obj=None, **kwargs):
        formset = super().get_formset(request, obj, **kwargs)
        formset.instance = obj
        return formset


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False
    verbose_name_plural = "Profile"
    fk_name = "user"
    extra = 0


@admin.register(UserWebAuthnCredential)
class UserWebAuthnCredentialAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "created_at", "sign_count")
    list_filter = ("created_at",)
    search_fields = ("user__email",)
    raw_id_fields = ("user",)
    readonly_fields = ("credential_id", "public_key", "sign_count", "created_at")


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm
    form = CustomUserChangeForm
    model = CustomUser
    inlines = (ProfileInline,)

    list_display = ("username", "email", "is_staff", "is_active", "trusted_binding_hash", "email_verified_at")
    search_fields = ("email", "username")
    ordering = ("email",)

    readonly_fields = (
        "credential_id",
        "credential_public_key",
        "credential_signature",
        "credential_user_handle",
    )

    fieldsets = (
        (None, {"fields": ("username", "email", "password")}),
        ("Passkey", {"fields": readonly_fields}),
        ("Device Trust", {"fields": ("trusted_binding_hash", "email_verified_at")}),
        ("Session", {"fields": ("preferred_session_lifetime_days",)}),
        ("Permissions", {"fields": ("is_staff", "is_active")}),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "username",
                    "email",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_active",
                    "preferred_session_lifetime_days",
                    "trusted_binding_hash",
                    "email_verified_at",
                ),
            },
        ),
    )


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    inlines = (ProfileAvatarInline, ProfileVideoInline)
    list_display = ("user", "bio", "phone", "date_of_birth", "location")
    search_fields = ("user__email", "user__username", "bio", "phone")


@admin.register(ActiveSession)
class ActiveSessionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "jti",
        "created_at_display",
        "expires_at_display",
        "last_active_display",
    )
    readonly_fields = (
        "created_at_display",
        "expires_at_display",
        "last_active_display",
    )

    def created_at_display(self, obj):
        return obj.created_at.strftime("%Y-%m-%d %H:%M:%S")

    created_at_display.short_description = "Created At"

    def expires_at_display(self, obj):
        return obj.expires_at.strftime("%Y-%m-%d %H:%M:%S")

    expires_at_display.short_description = "Expires At"

    def last_active_display(self, obj):
        return obj.last_active.strftime("%Y-%m-%d %H:%M:%S")

    last_active_display.short_description = "Last Active"


@admin.register(DeviceRecoveryCooldown)
class DeviceRecoveryCooldownAdmin(admin.ModelAdmin):
    list_display = ("user", "binding_hash", "cooldown_until")
    search_fields = ("user__email", "user__username", "binding_hash")


@admin.register(EmailVerificationOtp)
class EmailVerificationOtpAdmin(admin.ModelAdmin):
    list_display = ("user", "code_hash", "attempts", "expires_at", "consumed")
    search_fields = ("user__email", "user__username", "code_hash")


@admin.register(RecoveryEmailOtp)
class RecoveryEmailOtpAdmin(admin.ModelAdmin):
    list_display = ("user", "binding_hash", "code_hash", "attempts", "expires_at", "consumed")
    search_fields = ("user__email", "user__username", "binding_hash", "code_hash")

@admin.register(DeviceLoginChallenge)
class DeviceLoginChallengeAdmin(admin.ModelAdmin):
    list_display = ("user", "new_binding_hash", "code_hash", "attempts", "status", "expires_at")
    search_fields = ("user__email", "user__username", "new_binding_hash", "code_hash")