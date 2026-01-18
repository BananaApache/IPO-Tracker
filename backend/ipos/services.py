
import requests
from datetime import datetime
from django.conf import settings
from requests.exceptions import RequestException
from django.db import transaction
from .models import IPO
import logging

logger = logging.getLogger(__name__)

def fetch_ipo_data(from_date=None, to_date=None):
    if not from_date:
        from_date = datetime.now().strftime('%Y-%m-%d')
    if not to_date:
        to_date = from_date
    
    url = f"https://finnhub.io/api/v1/calendar/ipo?from={from_date}&to={to_date}&token={settings.FINNHUB_API_KEY}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except RequestException as e:
        logger.error(f"Finnhub API Error: {e}")
        raise e

def sync_ipos_to_db(data):
    """
    Takes the Finnhub JSON and maps IEAGU, FGIIU, etc., to our database.
    """
    ipo_list = data.get('ipoCalendar', [])
    if not ipo_list:
        logger.info("No IPOs found in the provided data.")
        return 0

    saved_count = 0

    with transaction.atomic():
        for item in ipo_list:
            try:
                obj, created = IPO.objects.update_or_create(
                    ticker=item.get('symbol'),
                    defaults={
                        'company_name': item.get('name', 'Unknown Company'),
                        'ipo_date': item.get('date'),
                    }
                )
                if created:
                    saved_count += 1
            except Exception as e:
                logger.error(f"Failed to sync IPO {item.get('symbol')}: {e}")
                continue 
                
    return saved_count
