from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.LoginView.as_view(), name="login"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("password-reset/", views.PasswordResetView.as_view(), name="password_reset"),
    path("password-reset/sent/", views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path("reset/<uidb64>/<token>/", views.PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("invite/<str:token>/", views.accept_invite, name="accept_invite"),
    path("users/", views.users_list, name="users"),
    path("users/invite/", views.invite_create, name="invite_create"),
    path("users/invite/<int:pk>/revoke/", views.invite_revoke, name="invite_revoke"),
    path("users/<int:pk>/role/", views.membership_role, name="membership_role"),
    path("users/<int:pk>/toggle/", views.membership_toggle, name="membership_toggle"),
]
