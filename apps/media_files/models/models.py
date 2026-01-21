import os
import subprocess
from io import BytesIO

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile, File
from django.core.validators import FileExtensionValidator
from django.db import models, transaction
from django.utils import timezone
from imagekit.models import ImageSpecField
from imagekit.processors import ResizeToFill
from PIL import Image
from polymorphic.models import PolymorphicModel


def ensure_primary_if_needed(instance):
    if instance.is_primary:
        return

    if not instance.content_type or not instance.object_id:
        if instance.content_object:
            instance.content_type = ContentType.objects.get_for_model(
                instance.content_object
            )
            instance.object_id = instance.content_object.pk
        else:
            return

    has_primary = (
        DisplayPhoto.objects.filter(
            content_type=instance.content_type,
            object_id=instance.object_id,
            is_primary=True,
        ).exists()
        or DisplayVideo.objects.filter(
            content_type=instance.content_type,
            object_id=instance.object_id,
            is_primary=True,
        ).exists()
    )

    if not has_primary:
        instance.is_primary = True


def set_primary(instance):
    if not instance.is_primary:
        return
    if not instance.content_type or not instance.object_id:
        if instance.content_object:
            instance.content_type = ContentType.objects.get_for_model(
                instance.content_object
            )
            instance.object_id = instance.content_object.pk
        else:
            return
    with transaction.atomic():
        DisplayPhoto.objects.filter(
            content_type=instance.content_type,
            object_id=instance.object_id,
            is_primary=True,
        ).exclude(pk=instance.pk).update(is_primary=False)
        DisplayVideo.objects.filter(
            content_type=instance.content_type,
            object_id=instance.object_id,
            is_primary=True,
        ).exclude(pk=instance.pk).update(is_primary=False)


class DisplayMedia(models.Model):
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True)
    object_id = models.PositiveIntegerField(null=True)
    content_object = GenericForeignKey("content_type", "object_id")

    is_primary = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(
                fields=["content_type", "object_id", "is_primary", "created_at"],
                name="dm_primary_created",
            ),
        ]

    def save(self, *args, **kwargs):
        is_new = self.pk is None

        super().save(*args, **kwargs)

        if is_new:
            ensure_primary_if_needed(self)

        if self.is_primary:
            set_primary(self)

        super().save(update_fields=["is_primary"])

    def concrete(self):
        if hasattr(self, "displayphoto"):
            return self.displayphoto
        if hasattr(self, "displayvideo"):
            return self.displayvideo
        return self


class DisplayPhoto(DisplayMedia):
    image = models.ImageField(
        upload_to="media/photos/%Y/%m/%d/",
        validators=[
            FileExtensionValidator(
                allowed_extensions=["jpg", "jpeg", "png", "webp", "gif"]
            )
        ],
    )

    image_thumbnail_small = ImageSpecField(
        source="image",
        processors=[ResizeToFill(180, 180)],
        format="WEBP",
        options={"quality": 60},
    )

    image_thumbnail_medium = ImageSpecField(
        source="image",
        processors=[ResizeToFill(640, 640)],
        format="WEBP",
        options={"quality": 80},
    )

    class Meta:
        ordering = ["-created_at"]


class DisplayVideo(DisplayMedia):
    video = models.FileField(
        upload_to="media/videos/%Y/%m/%d/",
        validators=[FileExtensionValidator(["mp4", "mov", "webm"])],
    )

    duration = models.FloatField(null=True, blank=True)

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("processing", "Processing"),
        ("done", "Done"),
        ("failed", "Failed"),
    ]

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="pending",
    )

    class Meta:
        ordering = ["-created_at"]


from django.conf import settings
from django.core.files.storage import FileSystemStorage

protected_storage = FileSystemStorage(location=settings.PROTECTED_MEDIA_ROOT)


class File(PolymorphicModel):
    file = models.FileField(
        max_length=255, null=True, blank=True, storage=protected_storage
    )
    name = models.CharField(max_length=255, blank=True, null=True)
    original_name = models.CharField(max_length=255, blank=True, null=True)
    extension = models.CharField(max_length=10, blank=True, null=True)
    category = models.CharField(max_length=20, blank=True, null=True)
    file_size = models.BigIntegerField(default=0)
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-uploaded_at"]
        indexes = [
            models.Index(fields=["uploaded_at"]),
            models.Index(fields=["category", "uploaded_at"]),
        ]

    def save(self, *args, **kwargs):
        if self.file:
            if not self.original_name:
                self.original_name = os.path.basename(self.file.name)
            if not self.name:
                self.name = os.path.basename(self.file.name)
            if not self.extension:
                self.extension = os.path.splitext(self.original_name)[1].lower()
            if not self.file_size and self.file.size:
                self.file_size = self.file.size
            if not self.category:
                self.category = self.determine_category(self.extension)
        super().save(*args, **kwargs)

    def determine_category(self, ext: str) -> str:
        ext = ext.lower()
        if ext in [
            ".jpg",
            ".jpeg",
            ".png",
            ".gif",
            ".webp",
            ".bmp",
            ".ico",
            ".svg",
            ".tiff",
        ]:
            return "image"
        if ext in [".mp4", ".mov", ".avi", ".webm", ".mkv", ".mpeg", ".flv", ".m4v"]:
            return "video"
        if ext in [".mp3", ".wav", ".ogg", ".flac", ".m4a"]:
            return "audio"
        if ext in [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt"]:
            return "document"
        return "other"

    def __str__(self):
        return self.original_name or self.name or f"File {self.id}"


class ImageFile(File):
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    thumbnail_small = models.ImageField(
        max_length=255,
        blank=True,
        null=True,
        upload_to="thumbnails/small/",
        storage=protected_storage,
    )
    thumbnail_medium = models.ImageField(
        max_length=255,
        blank=True,
        null=True,
        upload_to="thumbnails/medium/",
        storage=protected_storage,
    )
    dominant_color = models.CharField(max_length=7, blank=True, null=True)

    def get_average_color(self, img: Image.Image) -> str:
        img_small = img.resize((1, 1))
        pixel = img_small.getpixel((0, 0))
        r, g, b = pixel[:3]
        return f"#{r:02x}{g:02x}{b:02x}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.file:
            try:
                img = Image.open(self.file)
                self.width, self.height = img.size
                self.dominant_color = self.get_average_color(img)
                self.generate_thumbnails(img)
                super().save(
                    update_fields=[
                        "width",
                        "height",
                        "dominant_color",
                        "thumbnail_small",
                        "thumbnail_medium",
                    ]
                )
            except Exception as e:
                print(f"Image processing failed: {e}")

    def generate_thumbnails(self, img: Image.Image):
        thumb_small = img.copy()
        thumb_small.thumbnail((75, 75))
        thumb_small_io = BytesIO()
        thumb_small.save(
            thumb_small_io,
            format="WEBP",
            lossless=False,
            quality=50,
        )
        self.thumbnail_small.save(
            f"small_{os.path.basename(self.file.name)}.webp",
            ContentFile(thumb_small_io.getvalue()),
            save=False,
        )

        thumb_medium = img.copy()
        thumb_medium.thumbnail((800, 800))
        thumb_medium_io = BytesIO()
        thumb_medium.save(
            thumb_medium_io,
            format="WEBP",
            lossless=False,
            quality=80,
        )
        self.thumbnail_medium.save(
            f"medium_{os.path.basename(self.file.name)}.webp",
            ContentFile(thumb_medium_io.getvalue()),
            save=False,
        )


import logging

logger = logging.getLogger(__name__)


class VideoFile(File):
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        if self.file and (self.width is None or self.height is None):
            try:
                file_path = self.file.storage.path(self.file.name)
                cmd = [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "csv=s=x:p=0",
                    file_path,
                ]
                output = subprocess.check_output(cmd).decode().strip()
                w, h = map(int, output.split("x"))
                self.width, self.height = w, h
                super().save(update_fields=["width", "height"])
            except Exception as e:
                logger.error(f"Video processing failed for {self.file.name}: {e}")


from ..tasks.audio_waveform import process_audio_task

class AudioFile(File):
    duration = models.FloatField(null=True, blank=True)
    waveform = models.JSONField(null=True, blank=True)

    def save(self, *args, **kwargs):
        created = self.pk is None
        super().save(*args, **kwargs)

        if self.file and created:
            process_audio_task.delay(self.id)