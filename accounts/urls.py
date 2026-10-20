from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import OutboxPasswordResetForm

urlpatterns = [
    path("accounts/", views.accounts_page, name="accounts_page"),
    path("accounts/invite/", views.invite_all_view, name="invite_all"),
    path("accounts/<int:faculty_id>/invite/", views.invite_one_view, name="invite_one"),
    path("activate/<uidb64>/<token>/", views.activate, name="activate"),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(form_class=OutboxPasswordResetForm),
        name="password_reset",
    ),
    path("password-reset/sent/", auth_views.PasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("password-reset/done/", auth_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]
