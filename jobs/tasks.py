import logging
from celery import shared_task
from celery.signals import task_failure
from django.core.mail import send_mail
from django.conf import settings
from .rate_limiter import is_allowed
from .models import DeadLetterTask

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=5,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,       # exponential: 1s, 2s, 4s, 8s, 16s...
    retry_backoff_max=600,    # cap wait at 10 minutes
    retry_jitter=True,        # randomness added, avoids thundering herd
)
def send_transactional_email(self, to_email, subject, body):
    if not is_allowed():
        # Rate limit full — NOT a real failure, so this retry doesn't
        # consume the max_retries=5 budget meant for genuine errors.
        raise self.retry(countdown=1, max_retries=None)

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[to_email],
        fail_silently=False,
    )
    logger.info(f"Email sent to {to_email}")


@task_failure.connect(sender=send_transactional_email)
def handle_permanent_failure(sender=None, task_id=None, exception=None,
                              args=None, kwargs=None, **extra):
    DeadLetterTask.objects.create(
        task_id=task_id,
        task_name=sender.name,
        payload={"args": args, "kwargs": kwargs},
        exception=str(exception),
    )
    logger.error(f"Task {task_id} moved to dead letter: {exception}")