from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

admin.site.site_header = "Invigilation Duties — Admin"
admin.site.site_title = "Invigilation Duties"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("exams.urls")),
    path("", include("allocation.urls")),
    path("", include("reports.urls")),
    path("", include("notifications.urls")),
]
