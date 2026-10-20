from django.urls import path

from . import views

urlpatterns = [
    path("emails/", views.email_list, name="email_list"),
    path("emails/send/", views.send_emails_now, name="send_emails_now"),
    path("emails/<int:pk>/resend/", views.resend_email, name="resend_email"),
]
