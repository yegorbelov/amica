from django.contrib.auth import get_user_model
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from ..managers import CustomUserManager


class SessionLifetime(models.IntegerChoices):
    FIVE_SECONDS = 500, _("5 seconds")
    TEN_SECONDS = 1000, _("10 seconds")
    THIRTY_SECONDS = 3000, _("30 seconds")
    ONE_MINUTE = 6000, _("1 minute")
    ONE_WEEK = 7, _("1 week")
    TWO_WEEKS = 14, _("2 weeks")
    ONE_MONTH = 30, _("1 month")
    TWO_MONTHS = 60, _("2 months")
    THREE_MONTHS = 90, _("3 months")
    SIX_MONTHS = 180, _("6 months")


class CustomUser(AbstractUser):
    email = models.EmailField(_("email address"), unique=True)
    username = models.CharField(max_length=64, unique=False, blank=True, null=True)

    credential_id = models.BinaryField(null=True, blank=True)
    credential_public_key = models.BinaryField(null=True, blank=True)
    credential_signature = models.BinaryField(null=True, blank=True)
    credential_user_handle = models.BinaryField(null=True, blank=True)
    sign_count = models.BigIntegerField(default=0)

    preferred_session_lifetime_days = models.PositiveIntegerField(
        choices=SessionLifetime.choices, default=SessionLifetime.ONE_WEEK
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    @property
    def display_name(self):
        return self.username or self.email.split("@")[0] or "User"

    def __str__(self):
        return self.display_name


User = get_user_model()

from django.contrib.contenttypes.fields import GenericRelation


class Profile(models.Model):
    user = models.OneToOneField(
        CustomUser, on_delete=models.CASCADE, related_name="profile"
    )

    last_seen = models.DateTimeField(null=True, blank=True)
    bio = models.TextField(max_length=128, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=100, blank=True)
    profile_media = GenericRelation(
        "media_files.DisplayMedia", related_query_name="profile"
    )
    
    default_wallpaper_id = models.CharField(
        max_length=50, blank=True, null=True
    )

    active_wallpaper = models.ForeignKey(
        "Site.Wallpaper",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profiles_using",
    )
    
    def get_current_wallpaper_id(self):
        if self.active_wallpaper:
            return str(self.active_wallpaper.id)
        elif self.default_wallpaper_id:
            return self.default_wallpaper_id
        else:
            return "default-0"

    def update_last_seen(self):
        self.last_seen = timezone.now()
        self.save(update_fields=["last_seen"])

    def __str__(self):
        return f"Profile of {self.user.email}"

    class Meta:
        indexes = [
            models.Index(fields=["last_seen"]),
        ]


@receiver(post_save, sender=CustomUser)
def manage_user_profile(sender, instance, created, **kwargs):
    profile, _ = Profile.objects.get_or_create(user=instance)
    profile.save()


class ActiveSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sessions")

    jti = models.CharField(max_length=255, unique=True)

    refresh_token = models.TextField()

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_active = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "jti"]),
        ]
        ordering = ["-created_at"]

    def revoke(self):
        self.delete()

    def __str__(self):
        return f"Session(user={self.user_id}, active={self.last_active})"
