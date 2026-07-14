from django.test import TestCase, modify_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from .models import Customer, Order, OrderItem


@modify_settings(MIDDLEWARE={'remove': ['silk.middleware.SilkyMiddleware', 'tenants.middleware.TenantMiddleware']})
class OrderSummaryQueryCountTest(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name="Test", email="t@test.com")
        for _ in range(50):
            order = Order.objects.create(customer=self.customer, total_amount=10)
            OrderItem.objects.create(order=order, product_name="X", quantity=1, price=10)

    def test_summary_endpoint_does_not_scale_with_order_count(self):
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(f'/api/orders/summary/?customer_id={self.customer.id}')
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(ctx.captured_queries), 2)