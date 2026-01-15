from django.db import models

class IPO(models.Model):
    company_name = models.CharField(max_length=255)
    ticker = models.CharField(max_length=10, unique=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    ipo_date = models.DateField()
    sector = models.CharField(max_length=100, db_index=True) # Indexing for scalability
    is_mock = models.BooleanField(default=False) # To separate seed data from real data
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.company_name} ({self.ticker})"