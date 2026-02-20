from io import BytesIO

import requests
from django.core.files.base import ContentFile

from apps.media_files.models.models import DisplayPhoto

from ...accounts.models import CustomUser
import logging

logger = logging.getLogger(__name__)

def google_login_or_create_user(request, access_token):
    token_info_resp = requests.get(
        f"https://www.googleapis.com/oauth2/v1/tokeninfo?access_token={access_token}"
    )
    token_info = token_info_resp.json()

    if "error" in token_info:
        raise ValueError("Invalid Google access token")

    email = token_info.get("email")
    if not email:
        raise ValueError("Email not found in token")

    user_info_resp = requests.get(
        "https://www.googleapis.com/oauth2/v1/userinfo",
        params={"access_token": access_token},
    )
    user_info = user_info_resp.json()

    first_name = user_info.get("given_name", "")
    last_name = user_info.get("family_name", "")
    avatar_url = user_info.get("picture")

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
            logger.error("Failed to save Google avatar: %s", e)

    return user
