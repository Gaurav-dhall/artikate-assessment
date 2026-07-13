from rest_framework import serializers
from .models import Order

class OrderSummarySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name')
    item_count = serializers.SerializerMethodField()
    item_count = serializers.IntegerField()  # now comes from annotate(), not a per-row query


    class Meta:
        model = Order
        fields = ['id', 'status', 'total_amount', 'created_at', 'customer_name', 'item_count']

    def get_item_count(self, obj):
        return obj.items.count()