from django.urls import path

from . import views

urlpatterns = [
    path("faculty/", views.faculty_list, name="faculty_list"),
    path("faculty/new/", views.faculty_create, name="faculty_create"),
    path("faculty/designations/", views.categories, name="faculty_categories"),
    path("faculty/<int:pk>/", views.faculty_detail, name="faculty_detail"),
    path("faculty/<int:pk>/edit/", views.faculty_edit, name="faculty_edit"),
]
