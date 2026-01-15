from django.shortcuts import render

# Create your views here.
from rest_framework import generics
from .models import IPO
from .serializers import IPOSerializer

class IPOListCreateView(generics.ListCreateAPIView):
    queryset = IPO.objects.all().order_by('-ipo_date')
    serializer_class = IPOSerializer