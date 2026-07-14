from django.db import models

class DeadLetterTask(models.Model):
    task_id = models.CharField(max_length=255)
    task_name = models.CharField(max_length=255)
    payload = models.JSONField(default=dict)
    exception = models.TextField()
    failed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.task_name} ({self.task_id}) failed at {self.failed_at}"