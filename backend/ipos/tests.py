from django.test import TestCase

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from .models import IPO
from django.utils import timezone
from django.core.cache import cache

# Create your tests here.

class IPOTests(APITestCase):
    def setUp(self):
        cache.clear()
        
    def test_search_works(self):
        IPO.objects.create(
            company_name="TestCorp", 
            ticker="TCRP", 
            sector="Tech",
            ipo_date=timezone.now().date()
        )
        
        url = reverse('ipo-list') + '?search=TestCorp'
        response = self.client.get(url)
        
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 1)
    
    def test_rate_limiting(self):
        url = reverse('ipo-list')
        for _ in range(100):
            self.client.get(url)
        
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)