from django.urls import path

from . import views

from . import backups as backup_views
from . import operations
from . import preferences as preference_views

urlpatterns = [
    path("my/preferences/", preference_views.my_preferences, name="my_preferences"),
    path("my/preferences/<int:pk>/remove/", preference_views.remove_preference, name="remove_preference"),
    path("periods/<int:pk>/preferences/", preference_views.period_preferences, name="period_preferences"),
    path("backups/", backup_views.pool, name="backup_pool"),
    path("my/duties/<int:period_id>/acknowledge/", operations.acknowledge_duties, name="acknowledge_duties"),
    path("missed/", operations.missed_duties, name="missed_duties"),
    path("missed/obligations/<int:pk>/", operations.obligation_view, name="obligation"),
    path("periods/<int:pk>/duties/<int:duty_id>/move/", operations.move_duty_view, name="move_duty"),
    path("periods/<int:pk>/versions/", operations.roster_versions, name="roster_versions"),
    path("notifications/", operations.notifications, name="notifications"),
    path("faculty/<int:faculty_id>/periods/<int:period_id>/duties.pdf", operations.faculty_duty_slip, name="faculty_duty_slip"),
    path("periods/<int:pk>/backups/", backup_views.period_backups, name="period_backups"),
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
    path("search/", views.search, name="search"),
    path("attendance/", views.attendance, name="attendance"),
    path("attendance/slots/<int:slot_id>/", views.attendance_slot, name="attendance_slot"),
    path("attendance/duties/<int:duty_id>/", views.attendance_mark, name="attendance_mark"),
    path("attendance/duties/<int:duty_id>/stand-in/", views.record_stand_in_view, name="record_stand_in"),
    path("attendance/rooms/<int:room_id>/present/", views.attendance_room_present, name="attendance_room_present"),
    path("department/requests/", views.department_requests, name="department_requests"),
    path("requests/", views.admin_requests, name="admin_requests"),
    path("requests/<int:pk>/decide/", views.decide_request_view, name="decide_request"),
]
