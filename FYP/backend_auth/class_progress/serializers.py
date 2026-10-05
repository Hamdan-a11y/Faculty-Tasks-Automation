from rest_framework import serializers
from .models import ClassProgressLog

class ClassProgressLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = ClassProgressLog
        fields = '__all__'
