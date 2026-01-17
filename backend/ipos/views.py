from django.shortcuts import render

# Create your views here.
from rest_framework import generics, filters
from rest_framework.throttling import AnonRateThrottle
from .models import IPO
from .serializers import IPOSerializer

class IPOListCreateView(generics.ListCreateAPIView):
    queryset = IPO.objects.all().order_by('-ipo_date')
    serializer_class = IPOSerializer
    
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['company_name', 'ticker', 'sector']
    
    throttle_classes = [AnonRateThrottle]