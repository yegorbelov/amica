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
        "backup-codes/status/",
        views.backup_codes_status,
        name="backup_codes_status",
    ),
    path(
        "backup-codes/regenerate/",
        views.backup_codes_regenerate,
        name="backup_codes_regenerate",
    ),
    path("totp/setup/start/", views.totp_setup_start, name="totp_setup_start"),
    path("totp/setup/confirm/", views.totp_setup_confirm, name="totp_setup_confirm"),
    path("totp/disable/", views.totp_disable, name="totp_disable"),
    path("login/", views.api_login, name="api_login"),
    path(
        "device-login/submit-code/",
        views.device_login_submit_code,
        name="device_login_submit_code",
    ),
    path(
        "device-login/resend/",
        views.device_login_resend_notify,
        name="device_login_resend_notify",
    ),
    path(
        "device-login/trusted-decision/",
        views.device_login_trusted_decision,
        name="device_login_trusted_decision",
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
