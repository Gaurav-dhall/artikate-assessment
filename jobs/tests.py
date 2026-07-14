import uuid
import threading
from unittest.mock import patch

import redis
from django.conf import settings
from django.test import TestCase

from config.celery import app as celery_app
from .rate_limiter import is_allowed
from .models import DeadLetterTask
from .tasks import send_transactional_email, handle_permanent_failure


class RateLimiterAtomicityTest(TestCase):
    """
    Proves the rate limiter is atomic and never exceeds its configured
    limit under real concurrent access (simulating multiple Celery
    worker processes hitting Redis at the same instant).
    """

    def setUp(self):
        self.redis_client = redis.Redis.from_url(settings.CELERY_BROKER_URL)
        self.test_key = f"test_rate_limit_{uuid.uuid4()}"
        self.limit = 200

    def tearDown(self):
        self.redis_client.delete(self.test_key)

    def test_concurrent_requests_never_exceed_limit(self):
        results = []
        lock = threading.Lock()

        def hit():
            allowed = is_allowed(key=self.test_key, window=60, limit=self.limit)
            with lock:
                results.append(allowed)

        threads = [threading.Thread(target=hit) for _ in range(500)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed_count = sum(results)
        self.assertEqual(
            allowed_count, self.limit,
            f"Expected exactly {self.limit} of 500 concurrent requests to be "
            f"allowed, got {allowed_count}. A non-atomic implementation would "
            f"let more than {self.limit} through due to a race condition."
        )


class JobVolumeAndRetryTest(TestCase):
    """
    Uses Celery eager mode so suite runs without a live worker.
    task_eager_propagates=False lets Celery handle its internal Retry
    signal correctly instead of propagating it as a test exception.
    """

    def setUp(self):
        self._orig_eager = celery_app.conf.task_always_eager
        self._orig_propagates = celery_app.conf.task_eager_propagates
        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = False

    def tearDown(self):
        celery_app.conf.task_always_eager = self._orig_eager
        celery_app.conf.task_eager_propagates = self._orig_propagates

    @patch("jobs.tasks.send_mail")
    def test_500_jobs_all_processed_no_job_lost(self, mock_send_mail):
        with patch("jobs.tasks.is_allowed", return_value=True):
            for i in range(500):
                send_transactional_email.delay(
                    f"user{i}@example.com",
                    "Order Confirmation",
                    "Your order is confirmed."
                )

        self.assertEqual(
            mock_send_mail.call_count, 500,
            "Expected all 500 submitted jobs to reach send_mail exactly "
            "once each — any mismatch means a job was lost or duplicated."
        )

    def test_transient_failure_is_retried_and_eventually_succeeds(self):
        """
        Proves autoretry_for catches real exceptions and retries the task.
        In eager mode with task_eager_propagates=False, Celery catches
        the Retry signal internally and re-executes the task synchronously
        — exactly mimicking what a real worker does without a real queue.
        """
        call_count = {"value": 0}

        def smtp_then_success(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                raise Exception("SMTP timeout")

        with patch("jobs.tasks.is_allowed", return_value=True), \
             patch("jobs.tasks.send_mail", side_effect=smtp_then_success):
            send_transactional_email.delay(
                "retry-test@example.com", "OTP", "Your code is 123456"
            )

        self.assertEqual(
            call_count["value"], 2,
            "Expected exactly 2 attempts: initial SMTP failure plus one "
            "successful retry. autoretry_for should catch the exception "
            "and re-execute the task automatically."
        )


class DeadLetterHandlingTest(TestCase):
    """
    Tests dead-letter recording directly rather than running a task
    through 5 real exponential-backoff retries (~31 real seconds).
    """

    def test_permanently_failed_task_is_recorded(self):
        handle_permanent_failure(
            sender=send_transactional_email,
            task_id="fake-task-id-123",
            exception=Exception("Provider rejected after 5 attempts"),
            args=["permanently-bad@example.com", "Subject", "Body"],
            kwargs={},
        )

        self.assertEqual(DeadLetterTask.objects.count(), 1)
        entry = DeadLetterTask.objects.first()
        self.assertEqual(entry.task_id, "fake-task-id-123")
        self.assertIn("Provider rejected", entry.exception)