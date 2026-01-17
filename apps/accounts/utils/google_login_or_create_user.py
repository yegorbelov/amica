from io import BytesIO
from urllib.request import urlopen

import requests
from django.core.files.base import ContentFile

from apps.media_files.models.models import DisplayPhoto

from ...accounts.models import CustomUser


def google_login_or_create_user(request, id_token, client_id):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    data = google_id_token.verify_oauth2_token(
        id_token, google_requests.Request(), client_id
    )
    if not data or "email" not in data:
        raise ValueError("Invalid Google token")

    email = data["email"]
    first_name = data.get("given_name", "")
    last_name = data.get("family_name", "")
    avatar_url = data.get("picture")

    user, created = CustomUser.objects.get_or_create(
        email=email,
        defaults={
            "first_name": first_name,
            "last_name": last_name,
            "username": (first_name + "_" + last_name[:5]).lower(),
        },
    )

    profile = user.profile
    if avatar_url and created:
        try:
            response = requests.get(avatar_url, timeout=5)
            response.raise_for_status()
            img_temp = BytesIO(response.content)

            display_photo = DisplayPhoto(
                content_object=profile,
                is_primary=True,
            )
            display_photo.image.save(
                f"{user.email}_avatar.jpg", ContentFile(img_temp.read()), save=True
            )
            img_temp.close()
        except Exception as e:
            print("Failed to save Google avatar:", e)

    return user
