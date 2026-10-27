from django.urls import path

from . import dashboard, views

urlpatterns = [
    path("dashboard/", dashboard.dashboard, name="dashboard"),
    path("reports/", dashboard.report_center, name="report_center"),
    path("periods/<int:pk>/timetable/", dashboard.timetable, name="timetable"),
    path("dashboard/period/", dashboard.pick_period, name="pick_period"),
    path("periods/<int:pk>/export/roster.pdf", views.roster_pdf_view, name="roster_pdf"),
    path("periods/<int:pk>/export/roster.xlsx", views.roster_xlsx_view, name="roster_xlsx"),
    path("periods/<int:pk>/export/load.xlsx", views.load_xlsx_view, name="load_xlsx"),
    path("periods/<int:pk>/reports/", views.operations_report, name="operations_report"),
    path("periods/<int:pk>/reports.xlsx", views.operations_xlsx_view, name="operations_xlsx"),
]
