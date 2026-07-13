from django.db.models import Count
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import Order
from .serializers import OrderSummarySerializer

class OrderSummaryView(APIView):
    def get(self, request):
        customer_id = request.query_params.get('customer_id')
        # faulty code
        # orders = Order.objects.filter(customer_id=customer_id).order_by('-created_at')
        orders = (
            Order.objects
            .filter(customer_id=customer_id)
            .select_related('customer')
            .annotate(item_count=Count('items'))
            .order_by('-created_at')
        )
        serializer = OrderSummarySerializer(orders, many=True)
        return Response(serializer.data)