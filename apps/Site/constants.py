# constants.py
from django.contrib.contenttypes.models import ContentType

from apps.accounts.models.models import CustomUser, Profile

from .models import Chat, Contact

CT_CHAT = ContentType.objects.get_for_model(Chat).id
CT_USER = ContentType.objects.get_for_model(CustomUser).id
CT_CONTACT = ContentType.objects.get_for_model(Contact).id
CT_PROFILE = ContentType.objects.get_for_model(Profile).id
