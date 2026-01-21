from django.contrib import admin

from .models.models import *

admin.site.register(File)
admin.site.register(DisplayPhoto)
admin.site.register(DisplayVideo)
admin.site.register(ImageFile)
admin.site.register(VideoFile)
admin.site.register(AudioFile)
