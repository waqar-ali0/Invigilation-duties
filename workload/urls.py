from django.urls import path

from . import views

urlpatterns = [
    path("periods/<int:pk>/workload/", views.rules_page, name="workload_rules"),
    path("periods/<int:pk>/workload/people/", views.people, name="workload_people"),
    path("periods/<int:pk>/workload/rule/<int:rule_id>/", views.person_rule, name="workload_person_rule"),
    path("periods/<int:pk>/workload/versions/<int:number>/", views.version_detail, name="workload_version"),
]
