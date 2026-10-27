from django.urls import path

from . import views

urlpatterns = [
    path("whatsapp/", views.settings_page, name="whatsapp_settings"),
    path("whatsapp/templates/<str:kind>/", views.template_edit, name="whatsapp_template"),
    path("whatsapp/messages/", views.message_log, name="whatsapp_log"),
    path("whatsapp/tomorrow/", views.tomorrow, name="whatsapp_tomorrow"),
    path("whatsapp/send/", views.centre, name="whatsapp_centre"),
    path("whatsapp/contacts/<int:faculty_id>/", views.contact_edit, name="whatsapp_contact"),
    path("whatsapp/webhook/", views.webhook, name="whatsapp_webhook"),
    path("my/whatsapp/", views.my_whatsapp, name="my_whatsapp"),
]
