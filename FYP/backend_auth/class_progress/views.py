from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from auth_app.models import FacultyUser
from .automation import ZabdeskAutomation
from .models import ClassProgressLog
import json
from datetime import datetime

SESSION_KEY = "faculty_user_id"

def tracker_view(request):
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return redirect("auth_app:login")
        
    user_name = "Faculty"
    try:
        user = FacultyUser.objects.get(id=user_id)
        user_name = user.name or user.email
    except FacultyUser.DoesNotExist:
        pass

    return render(request, 'class_progress/tracker.html', {'user_name': user_name})

@csrf_exempt
def login_view(request):
    if request.method == 'POST':
        try:
            result = ZabdeskAutomation.open_post_class_progress()
            status_code = 200 if result.get("status") == "success" else 500
            return JsonResponse(result, status=status_code)
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"error": "Method not allowed"}, status=405)

@csrf_exempt
def auto_fill_view(request):
    if request.method == 'POST':
        try:
            result = ZabdeskAutomation.auto_fill()
            
            if result.get("status") == "success":
                data = result.get("data", {})
                # Save to DB
                ClassProgressLog.objects.create(
                    faculty_id="FAC001",
                    course=data.get("course_code", "Unknown"),
                    topic=data.get("topic", "Unknown"),
                    date=datetime.strptime(data.get("date"), "%Y-%m-%d").date() if data.get("date") else datetime.now().date()
                )
                return JsonResponse(result)
            else:
                return JsonResponse(result, status=500)
                
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def extract_noticeboard_view(request):
    if request.method == 'POST':
        result = ZabdeskAutomation.extract_noticeboard()
        status_code = 200 if result.get("status") == "success" else 500
        return JsonResponse(result, status=status_code)
    return JsonResponse({"error": "Method not allowed"}, status=405)
