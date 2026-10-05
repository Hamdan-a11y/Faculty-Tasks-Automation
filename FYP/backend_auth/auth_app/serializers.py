from rest_framework import serializers

from .models import ClassroomCourse, ClassroomCoursework, DriveFile, FacultyUser


class FacultyUserSerializer(serializers.ModelSerializer):
    profileImage = serializers.CharField(source="profile_image")
    googleId = serializers.CharField(source="google_id")
    lastLogin = serializers.DateTimeField(source="last_login")

    class Meta:
        model = FacultyUser
        fields = ["name", "email", "profileImage", "googleId", "lastLogin"]


class DriveFileSerializer(serializers.ModelSerializer):
    class Meta:
        model = DriveFile
        fields = [
            "id",
            "drive_file_id",
            "name",
            "mime_type",
            "size_bytes",
            "folder_id",
            "web_view_link",
            "web_content_link",
            "created_at",
        ]


class ClassroomCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassroomCourse
        fields = [
            "id",
            "course_id",
            "name",
            "section",
            "description_heading",
            "enrollment_code",
            "alternate_link",
            "state",
        ]


class ClassroomCourseworkSerializer(serializers.ModelSerializer):
    course_id = serializers.CharField(source="course_google_id")
    class Meta:
        model = ClassroomCoursework
        fields = [
            "id",
            "course_id",
            "coursework_id",
            "title",
            "description",
            "state",
            "alternate_link",
            "due_date",
            "due_time",
            "max_points",
            "drive_file_id",
            "created_at",
        ]
