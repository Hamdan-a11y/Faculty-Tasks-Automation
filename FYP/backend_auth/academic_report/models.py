from django.db import models


class AcademicReportRun(models.Model):
    """Stores a single report generation run with aggregated CLO/EPAN results."""

    # Course information
    course_code = models.CharField(max_length=64, blank=True)
    course_name = models.CharField(max_length=255, blank=True)
    course_instructor = models.CharField(max_length=255, blank=True)
    semester = models.CharField(max_length=64, blank=True)
    sections = models.CharField(max_length=64, blank=True, verbose_name="Sections (e.g. BS(SE)-1A)")
    total_credits = models.CharField(max_length=16, blank=True, verbose_name="Total Credits (e.g. 3 (2,1))")
    academic_term = models.CharField(max_length=64, blank=True, verbose_name="Academic Term (e.g. Fall 2024)")
    catalog_description = models.TextField(blank=True, verbose_name="Current Catalog Description")
    modifications_made = models.TextField(blank=True, default="None", verbose_name="Modifications Made to Course")

    # Source file info
    source_file_name = models.CharField(max_length=255, blank=True)
    total_students = models.PositiveIntegerField(default=0)

    # CLO averages (0.0-4.0 scale mapped from raw assessment averages)
    clo1_avg = models.FloatField(default=0.0)
    clo2_avg = models.FloatField(default=0.0)
    clo3_avg = models.FloatField(default=0.0)
    clo4_avg = models.FloatField(default=0.0)

    # EPAN average
    epan_avg = models.FloatField(default=0.0)

    # Achievement level
    achievement_level = models.CharField(max_length=32, blank=True)

    # Grade distribution (JSON: { "A": 5, "B": 10, ... })
    grade_distribution = models.JSONField(default=dict, blank=True)

    # Assessment averages (JSON with keys like "quiz1", "assign1", "mid_q1", etc.)
    assessment_averages = models.JSONField(default=dict, blank=True)

    # Report narrative fields
    student_feedback = models.TextField(blank=True)
    reflection = models.TextField(blank=True)
    proposed_improvements = models.TextField(blank=True)

    # Status tracking
    status = models.CharField(max_length=32, default="uploaded")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Report {self.pk or 'new'} - {self.course_code or 'Unknown'} ({self.status})"


class AcademicReportStudent(models.Model):
    """Per-student raw assessment scores linked to a report run."""

    run = models.ForeignKey(
        AcademicReportRun,
        on_delete=models.CASCADE,
        related_name="students",
    )

    student_name = models.CharField(max_length=255)
    registration_no = models.CharField(max_length=64, blank=True)

    # Quiz scores
    quiz1 = models.FloatField(default=0.0)
    quiz2 = models.FloatField(default=0.0)
    quiz3 = models.FloatField(default=0.0)
    quiz4 = models.FloatField(default=0.0)

    # Assignment scores
    assign1 = models.FloatField(default=0.0)
    assign2 = models.FloatField(default=0.0)
    assign3 = models.FloatField(default=0.0)
    assign4 = models.FloatField(default=0.0)

    # Mid Term per-question scores
    mid_q1 = models.FloatField(default=0.0)
    mid_q2 = models.FloatField(default=0.0)
    mid_q3 = models.FloatField(default=0.0)
    mid_q4 = models.FloatField(default=0.0)

    # Final Term per-question scores
    final_q1 = models.FloatField(default=0.0)
    final_q2 = models.FloatField(default=0.0)
    final_q3 = models.FloatField(default=0.0)
    final_q4 = models.FloatField(default=0.0)

    # Final Viva
    final_viva = models.FloatField(default=0.0)

    # Calculated per-student CLO scores
    clo1_score = models.FloatField(default=0.0)
    clo2_score = models.FloatField(default=0.0)
    clo3_score = models.FloatField(default=0.0)
    clo4_score = models.FloatField(default=0.0)

    # Total / Grade
    total_score = models.FloatField(default=0.0)
    grade_letter = models.CharField(max_length=4, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["student_name"]

    def __str__(self) -> str:
        return f"{self.student_name} ({self.registration_no or 'N/A'})"
