from django.contrib import admin
from django.contrib.contenttypes.admin import GenericTabularInline
from django.utils.html import format_html

from apps.media_files.models.models import DisplayPhoto, DisplayVideo

from .models import (
    Chat,
    ChatMember,
    Contact,
    Message,
    MessageReaction,
    MessageRecipient,
    Wallpaper,
    ChatMemberWallpaper,
    ChatWallpaper,
    UserWallpaper,
)


class ChatPhotoInline(GenericTabularInline):
    model = DisplayPhoto
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    verbose_name = "Photo"
    verbose_name_plural = "Photos"


class ChatVideoInline(GenericTabularInline):
    model = DisplayVideo
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    verbose_name = "Video"
    verbose_name_plural = "Videos"


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ["id", "name", "chat_type", "created_at"]
    list_filter = ["chat_type", "created_at"]
    inlines = [ChatPhotoInline, ChatVideoInline]


class ContactPhotoInline(GenericTabularInline):
    model = DisplayPhoto
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    verbose_name = "Photo"
    verbose_name_plural = "Photos"


class ContactVideoInline(GenericTabularInline):
    model = DisplayVideo
    ct_field = "content_type"
    ct_fk_field = "object_id"
    extra = 1
    verbose_name = "Video"
    verbose_name_plural = "Videos"


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "owner",
        "user",
        "name",
        "is_blocked",
        "is_favorite",
        "created_at",
    ]
    list_filter = ["is_blocked", "is_favorite", "created_at"]
    inlines = [ContactPhotoInline, ContactVideoInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "value", "chat", "user", "date"]
    list_filter = ["chat", "user", "date"]


admin.site.register(MessageReaction)
admin.site.register(MessageRecipient)
admin.site.register(ChatMember)
admin.site.register(Wallpaper)
admin.site.register(ChatMemberWallpaper)
admin.site.register(ChatWallpaper)
admin.site.register(UserWallpaper)
