from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("my/duties/", views.my_duties, name="my_duties"),
    path("my/duties/<int:period_id>/pdf/", views.my_duties_pdf, name="my_duties_pdf"),
    path("my/duties/<int:period_id>/ics/", views.my_duties_ics, name="my_duties_ics"),
    path("department/duties/", views.department_duties, name="department_duties"),
    path("department/duties/<int:period_id>/xlsx/", views.department_duties_xlsx, name="department_duties_xlsx"),
    path("my/unavailability/", views.my_unavailability, name="my_unavailability"),
    path("my/unavailability/<int:pk>/withdraw/", views.withdraw_request_view, name="withdraw_request"),
    path("staff/", views.staff_home, name="staff_home"),
    path("department/requests/", views.department_requests, name="department_requests"),
    path("requests/", views.admin_requests, name="admin_requests"),
    path("requests/<int:pk>/decide/", views.decide_request_view, name="decide_request"),
]
