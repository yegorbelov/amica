from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import *

router = DefaultRouter()
router.register(r"messages", MessageViewSet, basename="messages")

urlpatterns = [
    path("get_chats/", GetChats.as_view(), name="get_chats"),
    path("get_chat/<int:chat_id>/", GetChat.as_view(), name="get_chat"),
    path(
        "get_messages/<int:chat>/",
        GetMessagesAPIView.as_view(),
        name="get_messages",
    ),
    path("get_general_info/", get_general_info, name="get_general_info"),
    path("get_contacts/", GetContacts, name="get_contacts"),
    path("users/search/", UserEmailSearchView.as_view(), name="user-email-search"),
    path("protected-file/<int:file_id>/", ProtectedFileView.as_view(), name="protected-file-default"),
    path("protected-file/<int:file_id>/<str:version>/", ProtectedFileView.as_view(), name="protected-file-versioned"),
    
    path("wallpapers/", UserWallpapersAPIView.as_view(), name="wallpaper-list"),
]

urlpatterns += router.urls
