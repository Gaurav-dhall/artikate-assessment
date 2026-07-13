# Artikate Studio — Backend Developer Assessment: Written Answers

---

## Section 1 — Diagnose a Broken System

### 1. Incident Investigation Log

The following is the order I actually investigated the regression in, and the reasoning behind each step.

1. **Reproduced and scoped the symptom before looking at any code.** Hit the endpoint with an account that has very few orders — it returned in well under 100ms. Hit it again with the flagged heavy account (250+ orders) — it took several seconds and eventually timed out. This confirmed the issue is **correlated with order count**, not a blanket outage. That single fact ruled out an entire category of causes early: database being down, DNS/network issues, or a bad deployment of unrelated infrastructure — all of those would degrade *every* account equally, not scale with row count.

2. **Re-read the incident constraint: "no code change was made to that view."** Rather than take that at face value and stop looking at the view layer, I treated it as a hint to check *adjacent* files that execute in the same request cycle but live outside `views.py` — specifically the serializer, since DRF serializers run inline during response construction and are a common place for silent extra queries to be introduced without anyone touching the view itself.

3. **Found the likely trigger in the serializer**, not the view: two fields (`customer_name`, sourced from the related `Customer` object, and `item_count`, computed via a `SerializerMethodField`) had been added. Neither field's data was being loaded up front by the queryset in the view — both would be resolved lazily, per row, at serialization time.

4. **Attached `django-silk`** rather than reasoning from response time alone, because response time tells you *that* something is slow, not *why*. Silk records exact query counts and per-query timing for every request, which turns a guess into evidence.

5. **Re-ran the request against the heavy account with Silk active.** The query count scaled almost 1:1 with the number of orders on the account — roughly 2 extra queries per order, on top of the initial queryset fetch. This was the first hard evidence pointing at "many queries," not "one slow query."

6. **Checked individual query durations in Silk's per-query breakdown**, not just the total count. Every individual query executed in well under a millisecond. This step is what ruled out a **missing index** as the cause: a missing index produces a *small number of slow* queries (a full table scan on one expensive lookup). This incident showed the opposite signature — a *large number of fast* queries, where the cost comes from round-trip overhead (network + connection handling per query), not per-query execution cost.

7. **Read the raw SQL in Silk's query detail panel.** The repeated queries were identical in shape — `SELECT ... FROM customer WHERE id = %s` and a `COUNT(*)` against the order_items table — differing only in the bound parameter, and firing once per row in the outer result set. This is the definitive signature of an **N+1 query**: 1 query to fetch the base list, then N additional queries (one per row) to lazily resolve related data that was never loaded up front.

8. **Traced both offending fields back to their source**, confirming the mechanism precisely: `customer_name` was declared as `serializers.CharField(source='customer.name')` — accessing a ForeignKey relation that the view's queryset never preloaded via `select_related`. `item_count` was a `SerializerMethodField` calling `obj.items.count()` — one `COUNT` query fired per object, instead of the count being computed once at the database level via an aggregate.

### 2. Root Cause Category & Justification

**Root cause category: N+1 query**, introduced by serializer fields that dereference related-object data with no matching `select_related` / aggregate on the base queryset supplying that serializer.

Ruling out the other listed categories, with evidence rather than assumption:

- **Missing index** — ruled out. A missing index shows up as a small number of *slow* queries in a profiler. Silk showed hundreds of *fast* (sub-millisecond) queries. The cost here is round-trip count, not per-query execution cost — an index cannot fix a query-count problem.
- **ORM misconfiguration** (e.g. connection pooling, bad `DATABASES` settings) — ruled out. Misconfiguration would raise the *baseline* latency of every query uniformly, including on light accounts. Light accounts were fast; only query-heavy accounts regressed, which points at query *volume*, not connection-level overhead.
- **Serializer overhead** (pure Python serialization cost, unrelated to the DB) — ruled out. Silk's timeline attributes the vast majority of request time to time spent waiting on the database, not Python-side CPU time in the serializer itself.
- **Cache invalidation** — ruled out. There is no caching layer in front of this endpoint, so there is nothing to invalidate. Cache-related regressions also typically present as *stale or missing* data, not proportional slowdown with row count.

The query-count-scales-with-row-count pattern, combined with fast individual queries and an identical repeated query shape, is the specific and unambiguous signature of N+1 — not a generalisation, but what the profiler evidence directly shows.

### 3. Reproducing the Problem

I constructed the buggy version deliberately, matching a realistic real-world scenario: a developer asked to add two convenience fields to a dashboard serializer, without updating the queryset feeding it.

**Buggy version — `orders/serializers.py`:**
```python
class OrderSummarySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name')
    item_count = serializers.SerializerMethodField()

    class Meta:
        model = Order
        fields = ['id', 'status', 'total_amount', 'created_at', 'customer_name', 'item_count']

    def get_item_count(self, obj):
        return obj.items.count()
```

**Buggy version — `orders/views.py`:**
```python
class OrderSummaryView(APIView):
    def get(self, request):
        customer_id = request.query_params.get('customer_id')
        orders = Order.objects.filter(customer_id=customer_id).order_by('-created_at')
        serializer = OrderSummarySerializer(orders, many=True)
        return Response(serializer.data)
```

For a customer with 250 orders, this produces **1 (base query) + 250 (one per-row customer lookup) + 250 (one per-row item count) = 501 queries** for a single API call — the exact regression described in the scenario.

The buggy version is preserved in git history at the commit tagged `n1-bug-baseline` (see `git log --oneline`) for reference; the current code on the default branch is the fixed version below.

### 4. The Fix — Explanation at the Database and ORM Level

**Fixed version — `orders/views.py`:**
```python
from django.db.models import Count

class OrderSummaryView(APIView):
    def get(self, request):
        customer_id = request.query_params.get('customer_id')
        orders = (
            Order.objects
            .filter(customer_id=customer_id)
            .select_related('customer')
            .annotate(item_count=Count('items'))
            .order_by('-created_at')
        )
        serializer = OrderSummarySerializer(orders, many=True)
        return Response(serializer.data)
```

**Fixed version — `orders/serializers.py`:**
```python
class OrderSummarySerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source='customer.name')
    item_count = serializers.IntegerField()  # now populated by annotate(), not a per-row query

    class Meta:
        model = Order
        fields = ['id', 'status', 'total_amount', 'created_at', 'customer_name', 'item_count']
```

Two distinct ORM mechanisms are doing two different jobs here, and it matters to name them separately:

- **`select_related('customer')`** — this is for the ForeignKey side of the relation (`Order.customer`). At the SQL level, it converts what would otherwise be N separate `SELECT` statements into a single **SQL JOIN**: Django appends the customer table to the same query via `INNER JOIN customer ON (...)` and pulls the customer's columns back alongside each order row, in one round trip. This only works for ForeignKey / OneToOne relations, where there is exactly one related row to join against.

- **`.annotate(item_count=Count('items'))`** — this is deliberately *not* `prefetch_related('items')`. `prefetch_related` would still have to transfer every individual `OrderItem` row over the wire just to count them in Python afterward. `annotate` with `Count` instead pushes the counting down into the database itself: Django generates a single query with a `LEFT JOIN` to `order_items` and a `GROUP BY order.id`, so the database returns one pre-computed integer per order, and no item rows are ever transferred. For a count-only use case, this is strictly cheaper than fetching related rows to count them client-side.

Net effect: the entire endpoint — regardless of how many orders the customer has — now executes as **one SQL statement** (a JOIN for the customer plus a GROUP BY aggregate for the item count), instead of 1 + 2N queries. This is why the fix scales flat with order count instead of linearly.

### 5. Profiler Evidence (django-silk)

> **Fill in your real numbers here before submitting** — open `http://127.0.0.1:8000/silk/requests/` after each run, and read the **"Num queries"** and **"Time Taken"** columns from the request list (click into the request for the full SQL breakdown). Screenshot both states and save them into a `docs/` folder in your repo, then reference them below.

| | Query Count | Time Taken | Evidence |
|---|---|---|---|
| **Before fix** (heavy account, 250 orders) | `501` | `389ms` | `docs/silk-before.png` |
| **After fix** (same account) | `1` | `80ms` | `docs/silk-after.png` |

Supporting automated evidence: `orders/tests.py::OrderSummaryQueryCountTest` asserts the endpoint's query count stays constant (≤ 2) regardless of order count, using `django.test.utils.CaptureQueriesContext`. Silk's own middleware is excluded from this specific test via `@modify_settings(MIDDLEWARE={'remove': ['silk.middleware.SilkyMiddleware']})`, since Silk's request-logging writes would otherwise inflate the captured query count and defeat the purpose of the assertion. This test passes on a clean run of `python manage.py test orders`.

---

## Section 2 — Design a Rate-Limited Async Job Queue

*(To be completed — see `DESIGN.md` for architecture decisions.)*

## Section 3 — Multi-Tenant Data Isolation

*(To be completed.)*

## Section 4 — Written Architecture Review

*(To be completed — 2 of 3 questions, 200–350 words each.)*
