import os
from io import BytesIO
from PIL import Image
from django.db import models
from django.core.files.base import ContentFile

class ImageProcessingMixin(models.Model):
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    dominant_color = models.CharField(max_length=7, blank=True, null=True)

    class Meta:
        abstract = True

    IMAGE_FIELD_NAME = "image"
    THUMBNAILS = {}


    def process_image(self):
        image_field = getattr(self, self.IMAGE_FIELD_NAME)
        if not image_field:
            return

        img = Image.open(image_field)
        self.width, self.height = img.size
        self.dominant_color = self._get_average_color(img)

        for field_name, (size, quality) in self.THUMBNAILS.items():
            self._generate_thumbnail(
                img,
                image_field.name,
                field_name,
                size,
                quality,
            )

    def _get_average_color(self, img):
        r, g, b = img.resize((1, 1)).getpixel((0, 0))[:3]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _generate_thumbnail(self, img, name, field_name, size, quality):
        thumb = img.copy()
        thumb.thumbnail(size)
        io = BytesIO()
        thumb.save(io, format="WEBP", quality=quality)

        field = getattr(self, field_name)
        field.save(
            f"{field_name}_{os.path.basename(name)}.webp",
            ContentFile(io.getvalue()),
            save=False,
        )