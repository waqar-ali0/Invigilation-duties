from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("my/duties/", views.my_duties, name="my_duties"),
    path("my/unavailability/", views.my_unavailability, name="my_unavailability"),
    path("my/unavailability/<int:pk>/withdraw/", views.withdraw_request_view, name="withdraw_request"),
    path("staff/", views.staff_home, name="staff_home"),
]
