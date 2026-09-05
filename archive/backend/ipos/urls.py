from django.urls import path
from .views import IPOListCreateView, TriggerIPOSyncView

urlpatterns = [
    path('api/ipos/', IPOListCreateView.as_view(), name='ipo-list'),
   path('sync/', TriggerIPOSyncView.as_view(), name='sync-ipos'),
]