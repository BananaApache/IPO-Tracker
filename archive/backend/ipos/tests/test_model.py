from django.test import TestCase, override_settings

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from ..models import IPO
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
    
    @override_settings(REST_FRAMEWORK={
        'DEFAULT_THROTTLE_RATES': {'anon': '2/day'}
    })
    def test_rate_limiting(self):
        from rest_framework.throttling import AnonRateThrottle
        
        # Manually force the throttle to recognize the new rate
        # This overrides the internal cache of the rate limit
        AnonRateThrottle.rate = '2/day'
        AnonRateThrottle.num_requests, AnonRateThrottle.duration = AnonRateThrottle().parse_rate('2/day')

        # 1st request
        response = self.client.get(reverse('ipo-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 2nd request
        response = self.client.get(reverse('ipo-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 3rd request - should be 429
        response = self.client.get(reverse('ipo-list'))
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        
        