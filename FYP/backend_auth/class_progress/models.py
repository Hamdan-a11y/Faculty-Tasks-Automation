from django.db import models

class ClassProgressLog(models.Model):
    faculty_id = models.CharField(max_length=50)
    course = models.CharField(max_length=100)
    topic = models.CharField(max_length=255)
    date = models.DateField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.faculty_id} - {self.course} - {self.date}"
