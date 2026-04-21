from django.urls import path
from rest_framework.routers import DefaultRouter

from .message_chunk_upload_views import (
    MessageChunkBundleCompleteView,
    MessageChunkInitView,
    MessageChunkPartView,
)
from .views import *

router = DefaultRouter()
router.register(r"messages", MessageViewSet, basename="messages")

urlpatterns = [
    path("messages/chunk/init/", MessageChunkInitView.as_view()),
    path("messages/chunk/part/", MessageChunkPartView.as_view()),
    path("messages/chunk/complete/", MessageChunkBundleCompleteView.as_view()),
    path("get_chats/", GetChats.as_view(), name="get_chats"),
    path("get_chat/<int:chat_id>/", GetChat.as_view(), name="get_chat"),
    path(
        "get_messages/<int:chat>/",
        GetMessagesAPIView.as_view(),
        name="get_messages",
    ),
    path("get_general_info/", get_general_info, name="get_general_info"),
    path("users/search/", UserEmailSearchView.as_view(), name="user-email-search"),
    path("groups/search/", GroupSearchView.as_view(), name="group-search"),
    path("groups/create/", CreateGroupView.as_view(), name="group-create"),
    path("channels/create/", CreateChannelView.as_view(), name="channel-create"),
    path(
        "groups/<int:chat_id>/join/",
        JoinGroupView.as_view(),
        name="group-join",
    ),
    path(
        "groups/<int:chat_id>/leave/",
        LeaveGroupView.as_view(),
        name="group-leave",
    ),
    path(
        "protected-file/<int:file_id>/",
        ProtectedFileView.as_view(),
        name="protected-file-default",
    ),
    path(
        "protected-file/<int:file_id>/<str:version>/",
        ProtectedFileView.as_view(),
        name="protected-file-versioned",
    ),
    path(
        "protected-file/<int:file_id>/<str:file_type>/<str:version>/",
        ProtectedFileView.as_view(),
        name="protected-file-versioned",
    ),
    path("wallpapers/", UserWallpapersAPIView.as_view(), name="wallpaper-list"),
    path("contact/", ContactAPIView.as_view(), name="contact-list"),
]

urlpatterns += router.urls
