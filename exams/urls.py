from django.urls import path

from . import inventory, views

urlpatterns = [
    path("rooms/", inventory.room_list, name="room_list"),
    path("rooms/new/", inventory.room_create, name="room_create"),
    path("rooms/<int:pk>/edit/", inventory.room_edit, name="room_edit"),
    path("periods/", views.period_list, name="period_list"),
    path("periods/<int:pk>/", views.period_dashboard, name="period_dashboard"),
    path("periods/<int:pk>/import/", views.schedule_import, name="schedule_import"),
    path("periods/<int:pk>/availability/open/", views.open_availability_view, name="open_availability"),
    path("periods/<int:pk>/availability/close/", views.close_availability_view, name="close_availability"),
    path("imports/faculty/", views.faculty_import, name="faculty_import"),
    path("imports/departments/", views.department_import, name="department_import"),
]
