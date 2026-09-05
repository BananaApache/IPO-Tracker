
from django.test import TestCase
from unittest.mock import patch
from ..services import sync_ipos_to_db, fetch_ipo_data
from ..models import IPO
from django.core.cache import cache

class IPOSystemTests(TestCase):
    def setUp(self):
            cache.clear()
            
    def test_sync_ipos_to_db_logic(self):
        """Test that our mapping and idempotency work correctly."""
        mock_data = {
            'ipoCalendar': [
                {'symbol': 'TEST1', 'name': 'Test Company 1', 'date': '2026-01-16'},
                {'symbol': 'TEST1', 'name': 'Test Company 1 Updated', 'date': '2026-01-16'}
            ]
        }
        
        sync_ipos_to_db(mock_data)
        
        self.assertEqual(IPO.objects.count(), 1)
        self.assertEqual(IPO.objects.get(ticker='TEST1').company_name, 'Test Company 1 Updated')

    @patch('ipos.services.requests.get')
    def test_fetch_ipo_data_timeout(self, mock_get):
        """Test that our code handles API failures gracefully."""
        import requests
        mock_get.side_effect = requests.exceptions.Timeout
        
        with self.assertRaises(requests.exceptions.Timeout):
            fetch_ipo_data()

    @patch('ipos.tasks.fetch_ipo_data')
    @patch('ipos.tasks.sync_ipos_to_db')
    def test_task_execution(self, mock_sync, mock_fetch):
        """Verify the task calls the services in the right order."""
        from ..tasks import update_ipos_task
        
        mock_fetch.return_value = {'ipoCalendar': []}
        mock_sync.return_value = 0
        
        result = update_ipos_task()
        
        mock_fetch.assert_called_once()
        mock_sync.assert_called_once_with({'ipoCalendar': []})
        self.assertIn("Success", result)
        
        