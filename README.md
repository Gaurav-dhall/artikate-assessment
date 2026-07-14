# Artikate Studio — Backend Developer Assessment

A Django REST Framework backend demonstrating N+1 query diagnosis and resolution, a rate-limited async job queue with Celery, multi-tenant data isolation, and written architecture analysis.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Framework | Django 6.0, Django REST Framework 3.17 |
| Async Queue | Celery 5.6 |
| Message Broker | Redis |
| Database | SQLite (default, no setup needed) |
| Profiler | django-silk |
| Email | Django console backend (prints to terminal) |

---

## Requirements

- **Python** 3.12+
- **Redis** (for Celery broker — required only for Section 2)
- **pip** (comes with Python)
- **Git**

---

## Quick Start (under 5 minutes)

### 1. Clone the repository

```bash
git clone https://github.com/Gaurav-dhall/artikate-assessment.git
cd artikate-assessment
```

### 2. Create and activate a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Run database migrations

```bash
python manage.py migrate
```

### 5. (Optional) Seed sample data

```bash
python manage.py seed_orders
```

This creates sample customers, orders, and order items for testing the Section 1 API endpoint.

### 6. Start the Django development server

```bash
python manage.py runserver
```

The server starts at **http://127.0.0.1:8000/**.

### 7. Start Redis (required for Section 2 only)

In a **separate terminal**:

```bash
# Ubuntu/Debian
sudo service redis-server start

# macOS (Homebrew)
brew services start redis

# Docker
docker run -d -p 6379:6379 redis:latest
```

Verify Redis is running:

```bash
redis-cli ping
# Expected output: PONG
```

### 8. Start the Celery worker (required for Section 2 only)

In a **separate terminal** (with the virtualenv activated):

```bash
cd artikate-assessment
source venv/bin/activate
celery -A config worker --loglevel=info
```

---

## Running Tests

Run all tests across all apps:

```bash
python manage.py test
```

Run tests for a specific app:

```bash
python manage.py test orders      # Section 1 — query count assertion
python manage.py test jobs         # Section 2 — rate limiter, retry, dead letter
python manage.py test tenants      # Section 3 — tenant isolation
```

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/orders/summary/?customer_id=<id>` | Order summary for a customer (Section 1) |
| — | `/silk/` | django-silk profiler dashboard |
| — | `/admin/` | Django admin panel |

> **Note:** The `TenantMiddleware` requires an `X-Tenant-ID` header on every request. For local testing, pass it via curl:
>
> ```bash
> curl -H "X-Tenant-ID: 1" http://localhost:8000/api/orders/summary/?customer_id=1
> ```

---

## Project Structure

```
artikate-assessment/
├── config/                  # Django project configuration
│   ├── settings.py          # Settings (DB, Celery, middleware, installed apps)
│   ├── urls.py              # Root URL routing
│   ├── celery.py            # Celery app initialization
│   ├── wsgi.py
│   └── asgi.py
│
├── orders/                  # Section 1 — N+1 query diagnosis & fix
│   ├── models.py            # Customer, Order, OrderItem models
│   ├── views.py             # OrderSummaryView (select_related + annotate)
│   ├── serializers.py       # OrderSummarySerializer
│   ├── urls.py              # /api/orders/summary/ route
│   ├── tests.py             # Query count assertion test
│   └── management/          # seed_orders management command
│
├── jobs/                    # Section 2 — Rate-limited async job queue
│   ├── tasks.py             # send_transactional_email Celery task
│   ├── rate_limiter.py      # Redis sliding-window rate limiter
│   ├── models.py            # DeadLetterTask model
│   └── tests.py             # Rate limiter, retry, dead letter tests
│
├── tenants/                 # Section 3 — Multi-tenant data isolation
│   ├── models.py            # Tenant model
│   ├── middleware.py         # TenantMiddleware (resolves tenant per request)
│   ├── managers.py          # TenantManager (auto-scopes querysets)
│   ├── context.py           # contextvars-based tenant context
│   └── tests.py             # Tenant isolation tests
│
├── docs/                    # Profiler screenshots
│   ├── silk-before.png      # Silk screenshot — before fix (N+1)
│   └── silk-after.png       # Silk screenshot — after fix (1 query)
│
├── ANSWERS.md               # Written answers for all 4 sections
├── DESIGN.md                # Design decisions and architecture notes
├── requirements.txt         # Python dependencies
├── manage.py                # Django management script
└── .gitignore
```

---

## Section Overview

| Section | What it covers | Key files |
|---|---|---|
| **1 — Diagnose a Broken System** | Identified and fixed an N+1 query in the order summary endpoint using `select_related` and `annotate(Count(...))` | `orders/views.py`, `orders/serializers.py`, `orders/tests.py` |
| **2 — Rate-Limited Async Job Queue** | Celery task with Redis sliding-window rate limiter, exponential backoff retries, and dead letter queue | `jobs/tasks.py`, `jobs/rate_limiter.py`, `jobs/models.py`, `jobs/tests.py` |
| **3 — Multi-Tenant Data Isolation** | Middleware + `contextvars` + custom manager for automatic queryset scoping per tenant | `tenants/middleware.py`, `tenants/context.py`, `tenants/managers.py`, `tenants/tests.py` |
| **4 — Written Architecture Review** | Django Admin performance at 500k rows, offset vs cursor pagination trade-offs | `ANSWERS.md` (Section 4) |

---

## Notes & Assumptions

- **Database:** SQLite is used for simplicity — no external database setup required. All queries and optimizations demonstrated are ORM-level and transfer directly to PostgreSQL.
- **Email:** Uses Django's `console.EmailBackend` — emails are printed to the terminal instead of being sent over SMTP.
- **Redis:** Only required for running the Celery worker (Section 2). Sections 1, 3, and 4 work without Redis.
- **Tenant header:** The `TenantMiddleware` blocks requests without a valid `X-Tenant-ID` header or matching subdomain. The orders test uses `@modify_settings` to bypass this for the query-count test, which is unrelated to tenancy.
- **Profiler:** django-silk is available at `/silk/` when the server is running. Screenshots from profiling are in the `docs/` folder.
