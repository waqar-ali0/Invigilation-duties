from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("periods/", views.period_list, name="period_list"),
    path("periods/<int:pk>/", views.period_dashboard, name="period_dashboard"),
    path("periods/<int:pk>/import/", views.schedule_import, name="schedule_import"),
    path("imports/faculty/", views.faculty_import, name="faculty_import"),
    path("imports/departments/", views.department_import, name="department_import"),
]
