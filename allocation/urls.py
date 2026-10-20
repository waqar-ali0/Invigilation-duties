from django.urls import path

from . import views

urlpatterns = [
    path("periods/<int:pk>/allocate/", views.run_allocation_view, name="run_allocation"),
    path("periods/<int:pk>/clear/", views.clear_draft_view, name="clear_draft"),
    path("periods/<int:pk>/rooms/<int:room_id>/", views.room_detail, name="room_detail"),
    path("periods/<int:pk>/rooms/<int:room_id>/add/", views.add_duty_view, name="add_duty"),
    path("periods/<int:pk>/duties/<int:duty_id>/remove/", views.remove_duty_view, name="remove_duty"),
    path("periods/<int:pk>/publish/", views.publish_view, name="publish"),
]
