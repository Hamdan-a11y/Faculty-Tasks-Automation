from django.db import models
from django.utils import timezone


class FacultyUser(models.Model):
    google_id = models.CharField(max_length=255, unique=True)
    name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(unique=True)
    profile_image = models.URLField(blank=True)
    last_login = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.email} ({self.google_id})"


class GoogleServiceToken(models.Model):
    user = models.OneToOneField(FacultyUser, on_delete=models.CASCADE, related_name="google_token")
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    token_expiry = models.DateTimeField(null=True, blank=True)
    scopes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Google token for {self.user.email}"

    @property
    def is_expired(self) -> bool:
        if not self.token_expiry:
            return True
        return self.token_expiry <= timezone.now()


class DriveFile(models.Model):
    user = models.ForeignKey(FacultyUser, on_delete=models.CASCADE, related_name="drive_files")
    drive_file_id = models.CharField(max_length=255, db_index=True)
    name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=255, blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    folder_id = models.CharField(max_length=255, blank=True)
    web_view_link = models.URLField(blank=True)
    web_content_link = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Drive file {self.name} ({self.drive_file_id})"


class ClassroomCourse(models.Model):
    user = models.ForeignKey(FacultyUser, on_delete=models.CASCADE, related_name="classroom_courses")
    course_id = models.CharField(max_length=128, db_index=True)
    name = models.CharField(max_length=255)
    section = models.CharField(max_length=255, blank=True)
    description_heading = models.CharField(max_length=255, blank=True)
    enrollment_code = models.CharField(max_length=64, blank=True)
    alternate_link = models.URLField(blank=True)
    state = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        unique_together = ("user", "course_id")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Course {self.name} ({self.course_id})"


class ClassroomCoursework(models.Model):
    user = models.ForeignKey(FacultyUser, on_delete=models.CASCADE, related_name="classroom_coursework")
    course = models.ForeignKey(ClassroomCourse, on_delete=models.CASCADE, related_name="coursework")
    course_google_id = models.CharField(max_length=128, db_index=True)
    coursework_id = models.CharField(max_length=128, db_index=True)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    state = models.CharField(max_length=64, blank=True)
    alternate_link = models.URLField(blank=True)
    due_date = models.DateField(null=True, blank=True)
    due_time = models.CharField(max_length=16, blank=True)
    max_points = models.FloatField(null=True, blank=True)
    drive_file_id = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("course_google_id", "coursework_id", "user")

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Coursework {self.title} ({self.coursework_id})"


class ZabdeskMarkEntryRun(models.Model):
    user = models.ForeignKey(
        FacultyUser,
        on_delete=models.SET_NULL,
        related_name="zabdesk_mark_entry_runs",
        null=True,
        blank=True,
    )
    marks_type = models.CharField(max_length=64)
    marks_type_label = models.CharField(max_length=64)
    source_file_name = models.CharField(max_length=255, blank=True)
    requested_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    failure_count = models.PositiveIntegerField(default=0)
    failures = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=32, default="completed")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.marks_type_label} run ({self.status})"


class MidtermEvaluationRun(models.Model):
    user = models.ForeignKey(
        FacultyUser,
        on_delete=models.SET_NULL,
        related_name="midterm_evaluation_runs",
        null=True,
        blank=True,
    )
    source_file_name = models.CharField(max_length=255, blank=True)
    source_file_path = models.TextField(blank=True)
    source_file_type = models.CharField(max_length=16, default=".xlsx")
    source_sheet_name = models.CharField(max_length=128, blank=True)
    max_marks = models.FloatField(default=30.0)
    total_students = models.PositiveIntegerField(default=0)
    pass_count = models.PositiveIntegerField(default=0)
    fail_count = models.PositiveIntegerField(default=0)
    pass_percentage = models.FloatField(default=0.0)
    highest_marks = models.FloatField(null=True, blank=True)
    highest_student_name = models.CharField(max_length=255, blank=True)
    lowest_marks = models.FloatField(null=True, blank=True)
    lowest_student_name = models.CharField(max_length=255, blank=True)
    average_marks = models.FloatField(null=True, blank=True)
    median_marks = models.FloatField(null=True, blank=True)
    grade_distribution = models.JSONField(default=dict, blank=True)
    performance_distribution = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, default="uploaded")
    error_message = models.TextField(blank=True)
    report_file_name = models.CharField(max_length=255, blank=True)
    report_generated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Midterm evaluation ({self.status})"


class MidtermEvaluationStudent(models.Model):
    run = models.ForeignKey(
        MidtermEvaluationRun,
        on_delete=models.CASCADE,
        related_name="students",
    )
    row_number = models.PositiveIntegerField(default=0)
    registration_no = models.CharField(max_length=64, blank=True)
    student_name = models.CharField(max_length=255, blank=True)
    midterm_marks = models.FloatField(null=True, blank=True)
    grade_letter = models.CharField(max_length=4, blank=True)
    gpa = models.FloatField(null=True, blank=True)
    performance_band = models.CharField(max_length=32, blank=True)
    risk_status = models.CharField(max_length=64, blank=True)
    is_pass = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["registration_no", "student_name"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"{self.registration_no} - {self.student_name}"


class StudentOutreachEmailLog(models.Model):
    user = models.ForeignKey(
        FacultyUser,
        on_delete=models.SET_NULL,
        related_name="student_outreach_email_logs",
        null=True,
        blank=True,
    )
    run = models.ForeignKey(
        MidtermEvaluationRun,
        on_delete=models.SET_NULL,
        related_name="outreach_email_logs",
        null=True,
        blank=True,
    )
    student = models.ForeignKey(
        MidtermEvaluationStudent,
        on_delete=models.SET_NULL,
        related_name="outreach_email_logs",
        null=True,
        blank=True,
    )
    dispatch_batch_id = models.CharField(max_length=64, blank=True, db_index=True)
    recipient_type = models.CharField(max_length=32, default="student")
    student_name = models.CharField(max_length=255, blank=True)
    registration_no = models.CharField(max_length=64, blank=True)
    email_address = models.EmailField()
    midterm_marks = models.FloatField(null=True, blank=True)
    grade_letter = models.CharField(max_length=4, blank=True)
    risk_level = models.CharField(max_length=64, blank=True)
    email_subject = models.CharField(max_length=255, blank=True)
    email_body = models.TextField(blank=True)
    status = models.CharField(max_length=16, default="failed")
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:  # pragma: no cover - display helper
        return f"Outreach {self.status} to {self.email_address}"
