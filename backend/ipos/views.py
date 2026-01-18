from django.shortcuts import render

# Create your views here.
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import generics, filters
from rest_framework.throttling import AnonRateThrottle
from .models import IPO
from .serializers import IPOSerializer
from django.core.cache import cache
from rest_framework.response import Response
from rest_framework.views import APIView
from .tasks import update_ipos_task

class IPOListCreateView(generics.ListCreateAPIView):
    queryset = IPO.objects.all().order_by('-ipo_date')
    serializer_class = IPOSerializer
    
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['company_name', 'ticker', 'sector']
    
    throttle_classes = [AnonRateThrottle]
    
    def list(self, request, *args, **kwargs):
        cache_key = f"ipo_list_{request.query_params.get('search', '')}"
        cached_data = cache.get(cache_key)

        if cached_data:
            return Response(cached_data)

        response = super().list(request, *args, **kwargs)
        cache.set(cache_key, response.data, 60)
        return response

class TriggerIPOSyncView(APIView):
    def post(self, request):
        # .delay() sends the task to the background immediately!
        task = update_ipos_task.delay() 
        return Response({"task_id": task.id, "status": "Processing in background..."})
    