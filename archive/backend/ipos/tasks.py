
import requests
from bs4 import BeautifulSoup
from celery import shared_task
from django.utils import timezone
from .models import IPO
from .services import fetch_ipo_data, sync_ipos_to_db
import os

@shared_task(
    bind=True, 
    autoretry_for=(Exception,), 
    retry_backoff=True,
    max_retries=5
)
def update_ipos_task(self):
    """
    If fetch_ipo_data fails, Celery will catch it and retry 5 times.
    """
    data = fetch_ipo_data()
    count = sync_ipos_to_db(data)
    
    secret = os.getenv("REVALIDATION_SECRET")
    requests.post(f"http://frontend:3000/api/revalidate?secret={secret}")
    
    return f"Success: Sync'd {count} IPOs"

