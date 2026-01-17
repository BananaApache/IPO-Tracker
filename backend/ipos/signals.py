

from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import IPO

@receiver([post_save, post_delete], sender=IPO)
def clear_ipo_cache(sender, instance, **kwargs):
    # !!! Kill the cache because the data changed!
    cache.clear()
