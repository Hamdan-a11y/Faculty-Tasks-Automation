from django.conf import settings
from django.db import models


COURSE_FIELD_HELP = (
    "Optional course reference. Replace with a ForeignKey to your Course model when available."
)


class CLO(models.Model):
    code = models.CharField(max_length=20, unique=True)
    description = models.TextField()
    domain = models.CharField(max_length=50, blank=True, null=True)
    bt_level = models.CharField(max_length=20, blank=True, null=True)
    course = models.CharField(max_length=120, blank=True, null=True, help_text=COURSE_FIELD_HELP)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.code}: {self.description[:40]}"


class Question(models.Model):
    text = models.TextField()
    assessment_type = models.CharField(max_length=50)
    source_filename = models.CharField(max_length=255, blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Q#{self.pk or 'new'} - {self.assessment_type}"


class CLOMapping(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="mappings")
    clo = models.ForeignKey(CLO, on_delete=models.CASCADE)
    score = models.FloatField()
    auto_mapped = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("question", "auto_mapped")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Q{self.question_id} -> {self.clo.code} ({self.score:.2f})"
