from django.urls import path

from . import views

urlpatterns = [
    path("imports/", views.import_home, name="import_home"),
    path("imports/new/<str:kind>/", views.start, name="import_start"),
    path("imports/new/<str:kind>/period/<int:period_pk>/", views.start, name="import_start_period"),
    path("imports/template/<str:kind>.xlsx", views.template_download, name="import_template"),
    path("imports/<int:pk>/", views.batch_view, name="import_batch"),
    path("imports/<int:pk>/columns/", views.columns, name="import_columns"),
    path("imports/<int:pk>/report.xlsx", views.batch_report, name="import_report"),
    path("imports/<int:pk>/cancel/", views.batch_cancel, name="import_cancel"),
]
