import random
from django.core.management.base import BaseCommand
from orders.models import Customer, Order, OrderItem

class Command(BaseCommand):
    help = "Seeds a heavy customer (250 orders) and a light one (3 orders) for comparison."

    def handle(self, *args, **options):
        Customer.objects.all().delete()

        heavy = Customer.objects.create(name="Heavy Test Customer", email="heavy@test.com")
        light = Customer.objects.create(name="Light Test Customer", email="light@test.com")

        self._create_orders(heavy, 250)
        self._create_orders(light, 3)

        self.stdout.write(self.style.SUCCESS(
            f"Seeded heavy customer id={heavy.id} (250 orders), "
            f"light customer id={light.id} (3 orders)."
        ))

    def _create_orders(self, customer, count):
        for _ in range(count):
            order = Order.objects.create(
                customer=customer,
                status=random.choice(['pending', 'paid', 'shipped']),
                total_amount=round(random.uniform(10, 500), 2),
            )
            for i in range(random.randint(1, 4)):
                OrderItem.objects.create(
                    order=order,
                    product_name=f"Product {i}",
                    quantity=random.randint(1, 3),
                    price=round(random.uniform(5, 100), 2),
                )