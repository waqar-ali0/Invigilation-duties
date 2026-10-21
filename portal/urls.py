from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("my/duties/", views.my_duties, name="my_duties"),
    path("my/duties/<int:period_id>/pdf/", views.my_duties_pdf, name="my_duties_pdf"),
    path("my/duties/<int:duty_id>/swap/", views.ask_colleague, name="ask_colleague"),
    path("my/swaps/", views.swap_requests, name="swap_requests"),
    path("my/swaps/<int:pk>/respond/", views.swap_respond, name="swap_respond"),
    path("my/swaps/<int:pk>/cancel/", views.swap_cancel, name="swap_cancel"),
    path("swaps/", views.admin_swaps, name="admin_swaps"),
    path("swaps/<int:pk>/decide/", views.swap_decide, name="swap_decide"),
    path("my/duties/<int:period_id>/ics/", views.my_duties_ics, name="my_duties_ics"),
    path("department/duties/", views.department_duties, name="department_duties"),
    path("department/duties/<int:period_id>/xlsx/", views.department_duties_xlsx, name="department_duties_xlsx"),
    path("my/unavailability/", views.my_unavailability, name="my_unavailability"),
    path("my/unavailability/<int:pk>/withdraw/", views.withdraw_request_view, name="withdraw_request"),
    path("staff/", views.staff_home, name="staff_home"),
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/slots/<int:slot_id>/", views.attendance_slot, name="attendance_slot"),
    path("attendance/duties/<int:duty_id>/", views.attendance_mark, name="attendance_mark"),
    path("attendance/duties/<int:duty_id>/stand-in/", views.record_stand_in_view, name="record_stand_in"),
    path("attendance/rooms/<int:room_id>/present/", views.attendance_room_present, name="attendance_room_present"),
    path("department/requests/", views.department_requests, name="department_requests"),
    path("requests/", views.admin_requests, name="admin_requests"),
    path("requests/<int:pk>/decide/", views.decide_request_view, name="decide_request"),
]
