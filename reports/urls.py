from django.urls import path

from . import views

urlpatterns = [
    path("periods/<int:pk>/export/roster.pdf", views.roster_pdf_view, name="roster_pdf"),
    path("periods/<int:pk>/export/roster.xlsx", views.roster_xlsx_view, name="roster_xlsx"),
    path("periods/<int:pk>/export/load.xlsx", views.load_xlsx_view, name="load_xlsx"),
    path("periods/<int:pk>/reports/", views.operations_report, name="operations_report"),
    path("periods/<int:pk>/reports.xlsx", views.operations_xlsx_view, name="operations_xlsx"),
]
