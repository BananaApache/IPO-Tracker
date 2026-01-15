from django.urls import path
from .views import IPOListCreateView

urlpatterns = [
    path('api/ipos/', IPOListCreateView.as_view(), name='ipo-list'),
]