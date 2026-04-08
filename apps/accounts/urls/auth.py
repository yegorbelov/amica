from django.urls import include, path

from apps.accounts import views

urlpatterns = [
    path(
        "client-binding/bootstrap/",
        views.client_binding_bootstrap,
        name="client_binding_bootstrap",
    ),
    path("verify-email-otp/", views.verify_email_otp, name="verify_email_otp"),
    path(
        "device-recovery/no-access/",
        views.device_recovery_no_access,
        name="device_recovery_no_access",
    ),
    path(
        "device-recovery/verify-otp/",
        views.recovery_verify_otp,
        name="recovery_verify_otp",
    ),
    path("login/", views.api_login, name="api_login"),
    path(
        "device-login/confirm/",
        views.device_login_confirm,
        name="device_login_confirm",
    ),
    path(
        "device-login/poll/<uuid:challenge_id>/",
        views.device_login_poll,
        name="device_login_poll",
    ),
    path("logout/", views.logout, name="logout"),
    path("signup/", views.signup, name="register"),
    path("google/", views.google_login, name="google_login"),
    path("refresh_token/", views.refresh_token, name="refresh_token"),
    path("passkey/register/start/", views.passkey_register_start),
    path("passkey/register/finish/", views.passkey_register_finish),
    path("passkey/auth/start/", views.passkey_auth_start),
    path("passkey/auth/finish/", views.passkey_auth_finish),
    path("active-sessions/", include("apps.accounts.urls.sessions")),
]
