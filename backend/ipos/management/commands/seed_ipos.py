import random
from django.core.management.base import BaseCommand
from ipos.models import IPO
from faker import Faker
from datetime import timedelta, date

class Command(BaseCommand):
    help = 'Seeds the database with 100,000 mock IPO records'

    def handle(self, *args, **kwargs):
        fake = Faker()
        total_records = 100000
        batch_size = 5000  # We insert 5000 at a time to stay memory-efficient
        sectors = ['Technology', 'Healthcare', 'Finance', 'Energy', 'Consumer Goods', 'Industrial']
        
        self.stdout.write(f'Seeding {total_records} records...')

        for i in range(0, total_records, batch_size):
            ipo_objs = []
            for _ in range(batch_size):
                company = fake.company()
                # Create a unique-ish ticker
                ticker = f"{fake.lexify('????').upper()}{random.randint(100, 999)}"
                
                ipo_objs.append(IPO(
                    company_name=company,
                    ticker=ticker[:10], # Ensure it fits max_length
                    price=random.uniform(10.0, 500.0),
                    ipo_date=fake.date_between(start_date='-5y', end_date='today'),
                    sector=random.choice(sectors),
                    is_mock=True
                ))
            
            # This is the high-performance part
            IPO.objects.bulk_create(ipo_objs, ignore_conflicts=True)
            self.stdout.write(f'Inserted {i + batch_size} records...')

        self.stdout.write(self.style.SUCCESS('Successfully seeded 100,000 IPOs!'))