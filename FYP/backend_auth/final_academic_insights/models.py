from django.db import models


class StudentRecord(models.Model):
    student_name = models.CharField(max_length=255)
    quiz1 = models.FloatField(default=0.0)
    quiz2 = models.FloatField(default=0.0)
    quiz3 = models.FloatField(default=0.0)
    quiz4 = models.FloatField(default=0.0)
    assign1 = models.FloatField(default=0.0)
    assign2 = models.FloatField(default=0.0)
    assign3 = models.FloatField(default=0.0)
    assign4 = models.FloatField(default=0.0)
    midterm = models.FloatField(default=0.0)
    final = models.FloatField(default=0.0)
    total = models.FloatField(default=0.0)
    status = models.CharField(max_length=10, default="Fail")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student_name"]

    def __str__(self):
        return f"{self.student_name} - Total: {self.total} ({self.status})"
