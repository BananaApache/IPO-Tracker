
from rest_framework import serializers
from .models import IPO

class IPOSerializer(serializers.ModelSerializer):
    class Meta:
        model = IPO
        fields = [
            'id',
            'company_name',
            'ticker',
            'price',
            'ipo_date',
            'sector',
            'is_mock',
            'created_at'
        ]
        read_only_fields = ['id', 'created_at']
