from django.db import models

class Tenant(models.Model):
    name = models.CharField(max_length=255)
    subdomain = models.SlugField(unique=True)  # e.g. "nike" in nike.yourapp.com

    def __str__(self):
        return self.name