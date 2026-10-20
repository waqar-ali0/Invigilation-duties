from django.urls import path

from . import views

urlpatterns = [
    path("periods/<int:pk>/allocate/", views.run_allocation_view, name="run_allocation"),
    path("periods/<int:pk>/clear/", views.clear_draft_view, name="clear_draft"),
]
