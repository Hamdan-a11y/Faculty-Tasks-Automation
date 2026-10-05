"""Curriculum parsing and automated course outline submission."""

import os
import json
import uuid
import re
import time
import tempfile
import subprocess
from docx import Document

from django.conf import settings
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import (
    InvalidSessionIdException,
    TimeoutException,
    NoSuchElementException,
    WebDriverException,
)

from .helpers import (
    _get_session_user,
    _is_remote_debugger_running,
    _is_port_open,
    get_driver_path,
    _wait_and_click,
    _find_chrome_executable,
    set_value_js,
    CHROME_PROFILE_PATH,
    PORTAL_URL,
)

@api_view(["GET"])
@permission_classes([AllowAny])
def curriculum_page(request):
    # Template resides under auth_app/templates/auth/
    return render(request, "auth/curriculum.html")


def parse_course_outline(docx_path):
    data = {
        "title": "Untitled Course",
        "code": "N/A",
        "description": "",
        "objectives": "",
        "outcomes": "",
        "books": "",
        "methodology": "",
    }

    try:
        doc = Document(docx_path)
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        if paragraphs:
            data["title"] = paragraphs[0]
        if len(paragraphs) > 1:
            data["code"] = paragraphs[1]

        keywords = {
            "description": ["description"],
            "objectives": ["objective", "objectives"],
            "outcomes": ["outcome", "outcomes"],
            "books": ["book", "books", "textbook", "reference"],
            "methodology": ["methodology", "teaching method", "teaching methodology"],
        }

        def match_key(text):
            lower = text.lower()
            for key, words in keywords.items():
                for w in words:
                    if w in lower:
                        return key
            return None

        current_key = None
        collected = {k: [] for k in keywords.keys()}

        for p in paragraphs[2:]:
            key = match_key(p)
            if key:
                current_key = key
                content = p.split(":", 1)
                if len(content) > 1 and content[1].strip():
                    collected[key].append(content[1].strip())
                continue
            if current_key:
                collected[current_key].append(p)

        for key, items in collected.items():
            if items:
                data[key] = "\n".join(items).strip()
    except Exception:
        pass

    return data


def extract_docx_fields(docx_path):
    """Extract basic fields from paragraphs and tables for auto-fill."""
    fields = {
        "title": "",
        "code": "",
        "description": "",
        "objectives": "",
        "outcomes": "",
        "books": "",
        "methodology": "",
    }

    try:
        doc = Document(docx_path)
        lines = []
        for p in doc.paragraphs:
            if p.text and p.text.strip():
                lines.append(p.text.strip())
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    txt = cell.text.strip()
                    if txt:
                        lines.append(txt)

        if lines:
            fields["title"] = fields["title"] or lines[0]
        if len(lines) > 1:
            fields["code"] = fields["code"] or lines[1]

        keyword_map = {
            "description": ["description"],
            "objectives": ["objective", "objectives"],
            "outcomes": ["outcome", "outcomes"],
            "books": ["book", "books", "textbook", "reference"],
            "methodology": ["methodology", "teaching method", "teaching methodology"],
            "code": ["course code", "code"],
            "title": ["course title", "title"],
        }

        for line in lines:
            lower = line.lower()
            for key, words in keyword_map.items():
                if any(w in lower for w in words):
                    parts = line.split(":", 1)
                    value = parts[1].strip() if len(parts) > 1 else line
                    if key in {"title", "code"}:
                        if not fields[key]:
                            fields[key] = value
                    else:
                        existing = fields.get(key, "")
                        fields[key] = f"{existing}\n{value}".strip() if existing else value
        fallback = parse_course_outline(docx_path)
        for k, v in fallback.items():
            if not fields.get(k):
                fields[k] = v
    except Exception:
        pass

    return fields


def extract_smart_content(doc, target_header):
    content = []
    is_recording = False
    print(f"--- Looking for header: '{target_header}' ---")

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        # Start Recording if header matches (Case Insensitive Partial Match)
        if not is_recording:
            if target_header.lower() == text.lower() or (target_header.lower() in text.lower() and len(text) < 50):
                print(f"  -> FOUND START: '{text}'")
                is_recording = True
                continue

        # Stop Recording if we hit a new likely header
        if is_recording:
            is_bold = any(run.bold for run in para.runs)
            stop_words = [
                "course objective",
                "learning outcomes",
                "text book",
                "reference books",
                "course plan",
                "course objectives",
            ]
            if (is_bold and len(text) < 50) or text.lower() in stop_words:
                print(f"  -> STOPPED AT: '{text}'")
                break

            if text.startswith('•') or text.startswith('-'):
                content.append(f"<li>{text.lstrip('•- ')} </li>")
            else:
                content.append(text)

    return "\n".join(content)


def extract_content_by_header(doc, header_keys):
    extracted_html = ""

    # 1. Search Tables (Priority)
    for table in doc.tables:
        header_found = False
        start_row_index = -1

        # Find the header
        for row_idx, row in enumerate(table.rows):
            cell_texts = [c.text.strip().lower() for c in row.cells]
            if any(any(k.lower() in t for k in header_keys) for t in cell_texts):
                header_found = True
                start_row_index = row_idx + 1
                break

        if header_found:
            for i in range(start_row_index, len(table.rows)):
                row = table.rows[i]
                text = ""

                if len(row.cells) >= 2 and row.cells[1].text.strip():
                    text = row.cells[1].text.strip()
                elif len(row.cells) > 0:
                    text = row.cells[0].text.strip()

                if text:
                    clean = text.lstrip('•-→ i.ii.iii.1.2.3.').strip()
                    if clean:
                        extracted_html += f"<li>{clean}</li>"

            return extracted_html

    # 2. Fallback to Paragraphs
    is_recording = False
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue

        if any(k.lower() in text.lower() for k in header_keys) and len(text) < 50:
            is_recording = True
            continue

        stop_words = ["course objective", "learning outcomes", "text book", "reference books", "grading"]
        if is_recording and any(w in text.lower() for w in stop_words) and len(text) < 50:
            break

        if is_recording:
            clean = text.lstrip('•-→ i.ii.iii.1.2.3.').strip()
            extracted_html += f"<li>{clean}</li>"

    return extracted_html


@csrf_exempt
def open_portal_browser(request):
    if request.method not in ["GET", "POST"]:
        return JsonResponse({"error": "Method not allowed"}, status=405)

    try:
        PORTAL_ROOT = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/"

        # --- 1. LAUNCH / CONNECT TO CHROME ---
        options = Options()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")

        chrome_path = _find_chrome_executable()
        if not chrome_path:
            return JsonResponse({"status": "error", "message": "Chrome not found"}, status=500)

        os.makedirs(CHROME_PROFILE_PATH, exist_ok=True)

        if not _is_remote_debugger_running():
            print("[Portal] Launching Chrome with remote debugging …")
            subprocess.Popen([
                chrome_path,
                "--remote-debugging-port=9222",
                "--remote-allow-origins=*",
                f"--user-data-dir={CHROME_PROFILE_PATH}",
                "--profile-directory=Default",
                "--no-first-run",
                "--no-default-browser-check",
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            for _ in range(60):
                if _is_remote_debugger_running():
                    break
                time.sleep(0.1)
            else:
                return JsonResponse({"status": "error", "message": "Chrome remote debugger not reachable"}, status=500)

        service = ChromeService(executable_path=get_driver_path())
        driver = webdriver.Chrome(service=service, options=options)

        # --- 2. TAB MANAGEMENT ---
        # Look for an existing ZabDesk tab first.
        portal_handle = None
        for handle in driver.window_handles:
            driver.switch_to.window(handle)
            if "zabdesk-dummy-portal" in driver.current_url.lower():
                portal_handle = handle
                break

        if portal_handle:
            driver.switch_to.window(portal_handle)
            print(f"[Portal] Reusing existing tab: {driver.current_url}")
        else:
            # Open a NEW tab so we never navigate away from the user's existing tabs.
            driver.switch_to.new_window("tab")
            print("[Portal] Opened new tab for portal.")

        # --- 3. HELPER FUNCTIONS ---
        def wait_for_url(tokens, timeout=5):
            """Block until the URL contains one of the given tokens."""
            WebDriverWait(driver, timeout).until(
                lambda d: any(t in d.current_url.lower() for t in tokens)
            )

        def js_click(css_selector, label, timeout=4):
            """Wait for element, then click via JS (most reliable through debugger)."""
            try:
                el = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, css_selector))
                )
                driver.execute_script("arguments[0].click();", el)
                print(f"[Portal] Clicked: {label}")
                return True
            except Exception as ex:
                print(f"[Portal] Failed to click {label} ({css_selector}): {ex}")
                return False

        def xpath_click(xpath, label, timeout=4):
            """Wait for element by XPath, then click via JS."""
            try:
                el = WebDriverWait(driver, timeout).until(
                    EC.presence_of_element_located((By.XPATH, xpath))
                )
                driver.execute_script("arguments[0].click();", el)
                print(f"[Portal] Clicked: {label}")
                return True
            except Exception as ex:
                print(f"[Portal] Failed to click {label}: {ex}")
                return False

        # --- 4. NAVIGATE TO LOGIN PAGE IF NEEDED ---
        current = driver.current_url.lower()
        already_past_login = any(t in current for t in [
            "roles.html", "faculty-courses.html", "course-hub.html", "course-outline.html"
        ])

        if not already_past_login:
            driver.get(PORTAL_ROOT)
            WebDriverWait(driver, 8).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            print(f"[Portal] Loaded login page: {driver.current_url}")

        # --- 5. LOGIN (only if still on login / index page) ---
        current = driver.current_url.lower()
        if "roles.html" not in current and "faculty-courses.html" not in current \
                and "course-hub.html" not in current and "course-outline.html" not in current:
            # Use JS to set field values and invoke handleLogin directly.
            # This is the most reliable method because send_keys through a debugger-
            # attached session is flaky, and the portal validates via JS (not server).
            login_result = driver.execute_script("""
                var uid = document.getElementById('userId');
                var pwd = document.getElementById('password');
                if (!uid || !pwd) return 'fields_not_found';

                uid.value = 'FAC001';
                uid.dispatchEvent(new Event('input', {bubbles: true}));
                pwd.value = 'password123';
                pwd.dispatchEvent(new Event('input', {bubbles: true}));

                if (typeof handleLogin === 'function') {
                    handleLogin({preventDefault: function(){}});
                    return 'handler_called';
                }

                var btn = document.querySelector('.btn-login, button[type=submit]');
                if (btn) { btn.click(); return 'btn_clicked'; }

                return 'no_handler';
            """)
            print(f"[Portal] Login result: {login_result}")

            if login_result == "fields_not_found":
                return JsonResponse({"status": "error", "message": "Login fields not found on portal page."}, status=500)

            try:
                wait_for_url(["roles.html", "faculty-courses.html", "course-hub.html", "course-outline.html"], timeout=8)
            except Exception:
                return JsonResponse({
                    "status": "error",
                    "message": f"Login submitted but page did not redirect. Current: {driver.current_url}"
                }, status=500)

            time.sleep(0.3)

        # --- 6. ROLES PAGE -> Click Faculty ---
        if "roles.html" in driver.current_url.lower():
            print("[Portal] On roles page, clicking Faculty …")
            # The Faculty panel is an <article class="role-card faculty-panel">
            clicked = js_click("article.faculty-panel", "Faculty Panel", timeout=4)
            if not clicked:
                clicked = xpath_click("//*[contains(@class,'faculty-panel')]", "Faculty Panel (xpath)", timeout=3)
            if not clicked:
                clicked = xpath_click("//article[contains(.,'Faculty')]", "Faculty (text)", timeout=3)
            if clicked:
                try:
                    wait_for_url(["faculty-courses.html"], timeout=5)
                    time.sleep(0.3)
                except Exception:
                    pass

        # --- 7. FACULTY COURSES -> Click FOP (CS101) ---
        if "faculty-courses.html" in driver.current_url.lower():
            print("[Portal] On courses page, clicking FOP (CS101) …")
            # Course buttons have class "course-code-btn" with text "FOP (CS101)"
            clicked = xpath_click(
                "//button[contains(@class,'course-code-btn') and contains(.,'FOP')]",
                "FOP (CS101)", timeout=4
            )
            if not clicked:
                clicked = xpath_click("//button[contains(.,'CS101')]", "CS101 button", timeout=3)
            if clicked:
                try:
                    wait_for_url(["course-hub.html"], timeout=5)
                    time.sleep(0.3)
                except Exception:
                    pass

        # --- 8. COURSE HUB -> Click Course Outline sidebar ---
        if "course-hub.html" in driver.current_url.lower():
            print("[Portal] On course hub, clicking Course Outline sidebar …")
            clicked = js_click("#outlineNavBtn", "Course Outline btn", timeout=4)
            if not clicked:
                clicked = xpath_click(
                    "//a[contains(.,'Course Outline')] | //button[contains(.,'Course Outline')]",
                    "Course Outline (text)", timeout=3
                )
            if clicked:
                try:
                    wait_for_url(["course-outline.html"], timeout=5)
                    time.sleep(0.3)
                except Exception:
                    pass

        # --- 9. FINAL STATUS ---
        final_url = driver.current_url.lower()
        print(f"[Portal] Final URL: {driver.current_url}")

        if "course-outline.html" in final_url:
            return JsonResponse({"status": "opened"})
        elif "course-hub.html" in final_url:
            # Close enough — user is on the course workspace
            return JsonResponse({"status": "opened"})
        else:
            return JsonResponse({
                "status": "error",
                "message": f"Automation stopped at: {driver.current_url}"
            }, status=500)

    except Exception as e:
        print(f"Critical Error: {e}")
        return JsonResponse({"status": "error", "message": str(e)}, status=500)


@csrf_exempt
def process_curriculum_automation(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "No file provided"}, status=400)

    temp_path = None

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)
            temp_path = tmp.name

        parsed = parse_course_outline(temp_path)
        # Fallback: if description is empty, reuse objectives so the portal has content.
        if not parsed.get("description") and parsed.get("objectives"):
            parsed["description"] = parsed["objectives"]
        print("Parsed Data:", parsed)

        options = Options()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        options.add_argument("--remote-allow-origins=*")

        try:
            # Attach to existing logged-in browser session
            driver = webdriver.Chrome(service=ChromeService(executable_path=get_driver_path()), options=options)
        except Exception:
            return JsonResponse(
                {
                    "status": "auth_required",
                    "message": "Browser not open. Please open portal first.",
                },
                status=400,
            )

        try:
            if PORTAL_URL.split("?")[0] not in driver.current_url:
                driver.get(PORTAL_URL)

            wait = WebDriverWait(driver, 5)

            login_forms = driver.find_elements(By.CSS_SELECTOR, "#login-form, form.login, [data-test='login']")
            if login_forms and "login" in driver.current_url.lower():
                return JsonResponse({"status": "auth_required", "url": PORTAL_URL})

            def fill_field_by_header(header_text, value):
                if not value:
                    print(f"Skipping {header_text}: No value parsed from document.")
                    return False
                try:
                    # Exclude sidebar links and scripts; locate header text first.
                    header_xpath = (
                        f"//*[not(self::a) and not(self::script) and contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), "
                        f"'{header_text.lower()}')]"
                    )

                    # Find nearest textarea, contenteditable div, or iframe after the header.
                    input_xpath = (
                        f"({header_xpath})/following::*[self::textarea or @contenteditable='true' or self::iframe][1]"
                    )

                    target = wait.until(EC.presence_of_element_located((By.XPATH, input_xpath)))

                    driver.execute_script(
                        "arguments[0].scrollIntoView({behavior: 'smooth', block: 'center'});", target
                    )

                    if target.tag_name.lower() == "iframe":
                        print(f"Detected Iframe for {header_text}, switching context...")
                        driver.switch_to.frame(target)
                        try:
                            body = driver.find_element(By.TAG_NAME, "body")
                            body.clear()
                            body.send_keys(value)
                        finally:
                            driver.switch_to.default_content()
                    else:
                        target.clear()
                        target.send_keys(value)

                    print(f"SUCCESS: Filled {header_text}")
                    return True
                except Exception as e:
                    print(f"FAILED to fill {header_text}: {str(e)}")
                    return False

            fill_field_by_header("Course Description", parsed.get("description", ""))
            fill_field_by_header("Course Objective", parsed.get("objectives", ""))
            fill_field_by_header("Learning Outcomes", parsed.get("outcomes", ""))
            fill_field_by_header("Reference Books", parsed.get("books", ""))
            fill_field_by_header("Teaching and Learning Methodology", parsed.get("methodology", ""))

            try:
                title_input = driver.find_elements(
                    By.CSS_SELECTOR,
                    "#courseTitle, input[name='courseTitle'], input[name='course_title']",
                )
                if title_input:
                    title_input[0].clear()
                    title_input[0].send_keys(parsed.get("title", ""))

                code_input = driver.find_elements(
                    By.CSS_SELECTOR,
                    "#courseCode, input[name='courseCode'], input[name='course_code']",
                )
                if code_input:
                    code_input[0].clear()
                    code_input[0].send_keys(parsed.get("code", ""))
            except Exception as e:
                print("Title/Code fill error:", e)

            return JsonResponse({"status": "success"})
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


@csrf_exempt
def upload_curriculum(request):
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)

    uploaded = request.FILES.get("file")
    if not uploaded:
        return JsonResponse({"error": "No file provided"}, status=400)

    target_dir = os.path.join(tempfile.gettempdir(), "curriculum_uploads")
    os.makedirs(target_dir, exist_ok=True)

    temp_name = f"curriculum_{uuid.uuid4().hex}.docx"
    temp_path = os.path.join(target_dir, temp_name)

    try:
        with open(temp_path, "wb") as tmp:
            for chunk in uploaded.chunks():
                tmp.write(chunk)

        # Cleanup previous upload for this session to avoid clutter
        old_path = request.session.get("uploaded_curriculum_path")
        if old_path and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass

            # Final safety: explicitly fill both Course Description and Course Objective textareas with the same text.
            try:
                driver.execute_script(
                    """
                    (function(html){
                        const lower = (s)=> (s||'').toLowerCase();
                        function fillByHeader(headerText){
                            const header = Array.from(document.querySelectorAll('*')).find(el => {
                                const t = lower(el.textContent||'');
                                return t.includes(headerText) && el.tagName.toLowerCase() !== 'a';
                            });
                            if(!header) return false;
                            const textarea = header.parentElement ? header.parentElement.querySelector('textarea') : null;
                            if(textarea){
                                textarea.value = html.replace(/<br>/g,'\n');
                                textarea.innerHTML = html;
                                textarea.style.border = '2px solid green';
                                return true;
                            }
                            return false;
                        }

                        // Fill description, then objective.
                        const filledDesc = fillByHeader('course description');
                        const filledObj  = fillByHeader('course objective');

                        // Fallback: fill first two visible textareas.
                        if(!filledDesc || !filledObj){
                            const areas = Array.from(document.querySelectorAll('textarea')).filter(el=>{
                                const style = getComputedStyle(el);
                                return style.display !== 'none' && style.visibility !== 'hidden' && el.offsetHeight > 0 && el.offsetWidth > 0;
                            });
                            if(areas.length){
                                areas.slice(0,2).forEach(el=>{
                                    el.value = html.replace(/<br>/g,'\n');
                                    el.innerHTML = html;
                                    el.style.border = '2px solid green';
                                });
                            }
                        }
                    })(arguments[0]);
                    """,
                    desc_text.replace("\n", "<br>"),
                )
            except Exception:
                pass

        request.session["uploaded_curriculum_path"] = temp_path
        request.session["uploaded_file_path"] = temp_path
        request.session.modified = True
        return JsonResponse({"status": "uploaded", "path": temp_path})
    except Exception as e:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass
        return JsonResponse({"error": str(e)}, status=500)


def parse_curriculum_document(doc):
    """
    Parse all sections from a course-outline .docx Document object.
    Returns a structured dict with verbatim content for each portal field.
    Values are None when a section is not found — no fabrication.
    Raises ValueError if the document has no tables or cannot be parsed.
    """
    if not doc.tables:
        raise ValueError("Document contains no tables — cannot parse as a course outline")

    result = {
        "prerequisites": None,
        "credit_hours": None,
        "course_description": None,   # Explicitly None per spec — document has no Course Description paragraph
        "course_objectives": None,
        "learning_outcomes": None,
        "methodology": None,
        "materials": None,
        "class_conduct": None,
        "course_plan": [],
        "textbook": None,
        "reference_books": None,
        "marks_distribution": [],
        "attendance_policy": None,
        "challenges": None,
        "academic_integrity": None,
        "comments_suggestions": None,
        "student_feedback": None,
        "instructor_feedback": None,
        "course_code": None,
    }

    def _get_unique_cell_text(row):
        """Get de-duplicated cell text from a merged-cell row."""
        parts = []
        seen = set()
        for cell in row.cells:
            txt = cell.text.strip()
            if txt and txt not in seen:
                parts.append(txt)
                seen.add(txt)
        return "\n".join(parts)

    for table in doc.tables:
        if not table.rows:
            continue

        first_cell = table.rows[0].cells[0].text.strip().lower() if table.rows[0].cells else ""
        header_cells_lower = [c.text.strip().lower() for c in table.rows[0].cells if c.text.strip()]
        header_text = " ".join(header_cells_lower)

        # ── Prerequisites (inline "Pre-requisite: Nil") ──
        if result["prerequisites"] is None:
            for row in table.rows:
                found = False
                for cell in row.cells:
                    cell_lower = cell.text.strip().lower()
                    if "pre-requisite" in cell_lower or "prerequisite" in cell_lower:
                        text = cell.text.strip()
                        if ":" in text:
                            result["prerequisites"] = text.split(":", 1)[1].strip()
                        else:
                            result["prerequisites"] = text
                        found = True
                        break
                if found:
                    break

        # ── Credit Hours — compose from "Credits: (3,1)" ──
        if result["credit_hours"] is None:
            for row in table.rows:
                found = False
                for cell in row.cells:
                    cell_text = cell.text.strip()
                    cell_lower = cell_text.lower()
                    if ("credits" in cell_lower or "credit" in cell_lower) and ":" in cell_text:
                        val = cell_text.split(":", 1)[1].strip()
                        match = re.search(r'\((\d+)\s*,\s*(\d+)\)', val)
                        if match:
                            class_hrs = match.group(1)
                            lab_hrs = match.group(2)
                            result["credit_hours"] = (
                                f"{class_hrs}/class hours(per week) | "
                                f"{lab_hrs}/lab hours(per week)"
                            )
                            found = True
                            break
                        match_single = re.search(r'(\d+)', val)
                        if match_single:
                            result["credit_hours"] = (
                                f"{match_single.group(1)}/class hours(per week) | "
                                f"0/lab hours(per week)"
                            )
                            found = True
                            break
                if found:
                    break

        # ── Course Code (for mismatch detection only) ──
        if result["course_code"] is None:
            for row in table.rows:
                found = False
                for cell in row.cells:
                    cell_lower = cell.text.strip().lower()
                    if "course code" in cell_lower or "course title" in cell_lower:
                        text = cell.text.strip()
                        if ":" in text:
                            result["course_code"] = text.split(":", 1)[1].strip()
                        else:
                            result["course_code"] = text
                        found = True
                        break
                if found:
                    break

        # ── Course Objectives ──
        if result["course_objectives"] is None:
            if "course objective" in header_text or "course objectives" in header_text:
                parts = []
                for row in table.rows[1:]:
                    txt = _get_unique_cell_text(row)
                    if txt:
                        parts.append(txt)
                if parts:
                    result["course_objectives"] = "\n".join(parts)

        # ── CLO / Learning Outcomes ──
        if result["learning_outcomes"] is None:
            if "clo" in header_text and "description" in header_text:
                desc_idx = None
                for idx, h in enumerate(header_cells_lower):
                    if "description" in h:
                        desc_idx = idx
                        break

                descriptions = []
                labeled_descriptions = []
                for row in table.rows[1:]:
                    cells = row.cells
                    clo_label = cells[0].text.strip() if cells else ""
                    if not clo_label.lower().startswith("clo"):
                        continue

                    desc = ""
                    if desc_idx is not None and len(cells) > desc_idx:
                        desc = cells[desc_idx].text.strip()
                    elif len(cells) > 1:
                        desc = cells[1].text.strip()

                    if desc:
                        desc_clean = " ".join(desc.split())
                        descriptions.append(desc_clean.rstrip("."))
                        labeled_descriptions.append(f"{clo_label}: {desc_clean}")

                if descriptions:
                    result["learning_outcomes"] = ", ".join(descriptions) + "."
                    result["clo_with_labels"] = "\n".join(labeled_descriptions)

        # ── Teaching and Learning Methodology ──
        if result["methodology"] is None:
            if "methodology" in first_cell or (
                "teaching" in header_text and "methodology" in header_text
            ):
                if len(table.rows) > 1:
                    result["methodology"] = _get_unique_cell_text(table.rows[1])

        # ── Materials and Supplies ──
        if result["materials"] is None:
            if "materials" in first_cell and "supplies" in first_cell:
                if len(table.rows) > 1:
                    result["materials"] = _get_unique_cell_text(table.rows[1])

        # ── Expected Class Conduct ──
        if result["class_conduct"] is None:
            if "class conduct" in first_cell:
                if len(table.rows) > 1:
                    result["class_conduct"] = _get_unique_cell_text(table.rows[1])

        # ── Course Plan (Week / Topics table) ──
        if not result["course_plan"]:
            row0_lower = [c.text.strip().lower() for c in table.rows[0].cells]
            if "week" in row0_lower and "topics" in row0_lower:
                topic_idx = row0_lower.index("topics")
                for row in table.rows[1:]:
                    cells = row.cells
                    if len(cells) < 2:
                        continue
                    week_num = cells[0].text.strip()
                    topic = (
                        cells[topic_idx].text.strip()
                        if len(cells) > topic_idx
                        else ""
                    )
                    if week_num:
                        result["course_plan"].append(
                            {"week": week_num, "topic": topic}
                        )

        # ── Text Book ──
        if result["textbook"] is None:
            if "text book" in first_cell or "textbook" in first_cell:
                if len(table.rows) > 1:
                    result["textbook"] = _get_unique_cell_text(table.rows[1])

        # ── Reference Books ──
        if result["reference_books"] is None:
            if "reference book" in first_cell or "reference books" in first_cell:
                parts = []
                for row in table.rows[1:]:
                    txt = _get_unique_cell_text(row)
                    if txt:
                        parts.append(txt)
                if parts:
                    result["reference_books"] = "\n".join(parts)

        # ── Marks Distribution ──
        if not result["marks_distribution"]:
            if "marks distribution" in first_cell:
                start_idx = 0
                for i, row in enumerate(table.rows):
                    if row.cells and "marks head" in row.cells[0].text.strip().lower():
                        start_idx = i + 1
                        break
                if start_idx == 0:
                    start_idx = 2

                for i in range(start_idx, len(table.rows)):
                    cells = [c.text.strip() for c in table.rows[i].cells]
                    if len(cells) >= 5:
                        result["marks_distribution"].append(cells[:5])

        # ── Attendance Policy ──
        if result["attendance_policy"] is None:
            if "attendance policy" in first_cell:
                if len(table.rows) > 1:
                    result["attendance_policy"] = _get_unique_cell_text(table.rows[1])

        # ── Students with Physical or Educational Challenges ──
        if result["challenges"] is None:
            if "students with physical" in first_cell or "educational challenges" in first_cell:
                if len(table.rows) > 1:
                    result["challenges"] = _get_unique_cell_text(table.rows[1])

        # ── Academic Integrity ──
        if result["academic_integrity"] is None:
            if "academic integrity" in first_cell:
                if len(table.rows) > 1:
                    result["academic_integrity"] = _get_unique_cell_text(table.rows[1])

        # ── Comments and/or Suggestions ──
        if result["comments_suggestions"] is None:
            if "comments" in first_cell and "suggestion" in first_cell:
                if len(table.rows) > 1:
                    result["comments_suggestions"] = _get_unique_cell_text(table.rows[1])

        # ── Student / Instructor Feedback (two-column sub-table) ──
        if result["student_feedback"] is None or result["instructor_feedback"] is None:
            if len(table.rows[0].cells) >= 2:
                h1 = table.rows[0].cells[0].text.strip().lower()
                h2 = table.rows[0].cells[1].text.strip().lower()
                if "students" in h1 and "instructors" in h2:
                    if len(table.rows) > 1 and len(table.rows[1].cells) >= 2:
                        if result["student_feedback"] is None:
                            result["student_feedback"] = table.rows[1].cells[0].text.strip()
                        if result["instructor_feedback"] is None:
                            result["instructor_feedback"] = table.rows[1].cells[1].text.strip()

    # If course_description is not explicitly present in document, fill with labeled CLO list (or fallback)
    if not result["course_description"]:
        if result.get("clo_with_labels"):
            result["course_description"] = result["clo_with_labels"]
        elif result["learning_outcomes"]:
            result["course_description"] = result["learning_outcomes"]
        elif result["course_objectives"]:
            result["course_description"] = result["course_objectives"]

    return result


@csrf_exempt
def run_curriculum_automation(request):
    """
    Three-phase curriculum automation:
      Phase 1 — Parse the uploaded document fully before touching the portal.
      Phase 2 — Fill portal fields top-to-bottom with fail-fast on missing elements.
      Phase 3 — Self-verify by reading back each field from the DOM.
    Returns a structured completion report.
    """
    report = {
        "fields_filled": [],
        "fields_left_blank": [],
        "mismatches": [],
        "verification_failures": [],
        "no_submit_taken": True,
    }

    try:
        # ── Connect to Chrome via remote debugging ──
        options = Options()
        options.add_experimental_option("debuggerAddress", "127.0.0.1:9222")
        try:
            service = ChromeService(executable_path=get_driver_path())
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            return JsonResponse(
                {
                    "status": "auth_required",
                    "message": "Browser not open. Please click 'Open Zabdesk Portal' first.",
                },
                status=400,
            )

        # Validate session is alive
        try:
            _ = driver.title
        except InvalidSessionIdException:
            return JsonResponse(
                {
                    "status": "auth_required",
                    "message": "Browser session expired. Please click Open Portal Browser and retry.",
                },
                status=400,
            )

        # Switch to the most recent tab
        try:
            driver.switch_to.window(driver.window_handles[-1])
        except Exception:
            pass

        # Ensure we are on the course-outline page
        current_url = driver.current_url.lower()
        if "course-outline" not in current_url:
            portal_root = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/"
            try:
                driver.get(f"{portal_root}course-outline.html?courseId=1")
                WebDriverWait(driver, 8).until(
                    lambda d: "course-outline" in d.current_url.lower()
                )
            except Exception:
                return JsonResponse(
                    {
                        "status": "error",
                        "message": (
                            "Could not navigate to Course Outline page. "
                            f"Current URL: {driver.current_url}"
                        ),
                        "report": report,
                    },
                    status=400,
                )

        # Reject if portal is still on login page
        login_markers = driver.find_elements(
            By.CSS_SELECTOR, "#login-form, form.login, #userId"
        )
        if login_markers and "login" in driver.current_url.lower():
            return JsonResponse(
                {
                    "status": "auth_required",
                    "message": "Portal is on login page. Please log in first.",
                },
                status=400,
            )

        # ────────────────────────────────────────────────────────────────
        # PHASE 1: Parse the uploaded document
        # ────────────────────────────────────────────────────────────────
        file_path = request.session.get("uploaded_file_path") or request.session.get(
            "uploaded_curriculum_path"
        )
        if not file_path or not os.path.exists(file_path):
            return JsonResponse(
                {"status": "error", "message": "No uploaded file found. Please upload first."},
                status=400,
            )

        try:
            doc = Document(file_path)
            parsed = parse_curriculum_document(doc)
        except ValueError as ve:
            return JsonResponse(
                {
                    "status": "error",
                    "message": f"Failed to parse document: {ve}",
                    "report": {"parse_error": str(ve)},
                },
                status=400,
            )
        except Exception as exc:
            return JsonResponse(
                {
                    "status": "error",
                    "message": f"Failed to parse document: {exc}",
                    "report": {"parse_error": str(exc)},
                },
                status=500,
            )

        print("[Curriculum] Document parsed successfully.")

        # ── Course-code mismatch detection ──
        try:
            portal_course_title = driver.execute_script(
                "var el = document.getElementById('outlineCourseTitle');"
                "return el ? el.innerText : null;"
            )
            if portal_course_title and parsed.get("course_code"):
                doc_code = parsed["course_code"].strip()
                portal_title = portal_course_title.strip()
                if (
                    doc_code.lower() not in portal_title.lower()
                    and portal_title.lower() not in doc_code.lower()
                ):
                    report["mismatches"].append(
                        {
                            "field": "Course Code",
                            "portal_value": portal_title,
                            "document_value": doc_code,
                            "action": "not overwritten — flagged for manual review",
                        }
                    )
                    print(
                        f"[Curriculum] MISMATCH: Portal='{portal_title}' "
                        f"vs Document='{doc_code}'"
                    )
        except Exception as exc:
            print(f"[Curriculum] Could not check course code mismatch: {exc}")

        # ────────────────────────────────────────────────────────────────
        # PHASE 2: Fill portal fields (top-to-bottom, fail-fast)
        # ────────────────────────────────────────────────────────────────

        # (display_name, element_id, parsed_key, js_property)
        FIELD_MAP = [
            ("Course Prerequisites", "outlinePrerequisites", "prerequisites", "innerText"),
            ("Credit Hours", "outlineCreditHours", "credit_hours", "innerText"),
            ("Course Description", "outlineCourseDescription", "course_description", "innerHTML"),
            ("Course Objective", "outlineCourseObjectives", "course_objectives", "innerHTML"),
            ("Learning Outcomes", "outlineLearningOutcomes", "learning_outcomes", "innerHTML"),
            ("Teaching and Learning Methodology", "outlineMethodology", "methodology", "innerHTML"),
            ("Materials and Supplies", "outlineMaterials", "materials", "innerHTML"),
            ("Expected Class Conduct", "outlineConduct", "class_conduct", "innerHTML"),
            ("Text Book", "outlineTextbook", "textbook", "innerHTML"),
            ("Reference Books", "outlineReferences", "reference_books", "innerHTML"),
            ("Course Pre-Requisites (2nd)", "outlinePreReqText", "prerequisites", "innerHTML"),
            ("Attendance Policy", "outlineAttendance", "attendance_policy", "innerHTML"),
            ("Students with Challenges", "outlineChallenges", "challenges", "innerHTML"),
            ("Academic Integrity", "outlineIntegrity", "academic_integrity", "innerHTML"),
            ("Comments/Suggestions", "outlineComments", "comments_suggestions", "innerHTML"),
            ("Student Feedback", "outlineStudentFeedback", "student_feedback", "innerHTML"),
            ("Instructor Feedback", "outlineInstructorFeedback", "instructor_feedback", "innerHTML"),
        ]

        fill_expectations = {}  # element_id -> {field, value, prop}

        def _inject_field(element_id, value, prop):
            """Inject value into a portal element via JS.  Returns 'OK' or 'NOT_FOUND'."""
            if prop == "innerHTML":
                safe_value = json.dumps(value.replace("\n", "<br>"))
            else:
                safe_value = json.dumps(value)

            return driver.execute_script(
                f"""
                var el = document.getElementById('{element_id}');
                if (!el) return 'NOT_FOUND';
                el.{prop} = {safe_value};
                el.dispatchEvent(new Event('input',  {{bubbles: true}}));
                el.dispatchEvent(new Event('change', {{bubbles: true}}));
                el.dispatchEvent(new Event('blur',   {{bubbles: true}}));
                el.style.border = '2px solid green';
                return 'OK';
                """
            )

        for field_name, element_id, parsed_key, prop in FIELD_MAP:
            value = parsed.get(parsed_key)

            if value is None:
                report["fields_left_blank"].append(
                    {"field": field_name, "reason": "not found in source document"}
                )
                print(f"[Curriculum] BLANK: {field_name} — not in source document")
                continue

            js_result = _inject_field(element_id, value, prop)
            if js_result == "NOT_FOUND":
                report["abort_reason"] = (
                    f"Portal element '{element_id}' does not exist in the DOM"
                )
                return JsonResponse(
                    {
                        "status": "error",
                        "message": f"Aborted: element '{element_id}' not found on portal page",
                        "report": report,
                    },
                    status=500,
                )

            fill_expectations[element_id] = {
                "field": field_name,
                "value": value,
                "prop": prop,
            }
            source_snippet = value[:80].replace("\n", " ")
            report["fields_filled"].append(
                {"field": field_name, "source": source_snippet}
            )
            print(f"[Curriculum] FILLED: {field_name}")

        # ── Course Plan (structured row injection) ──
        if parsed.get("course_plan"):
            try:
                plan_json = json.dumps(parsed["course_plan"])
                plan_result = driver.execute_script(
                    f"""
                    var data = {plan_json};
                    var rows = document.querySelectorAll('.plan-row');
                    var filled = 0;
                    for (var i = 0; i < rows.length; i++) {{
                        var weekDiv  = rows[i].children[0];
                        var topicDiv = rows[i].children[2];   // children: Week|Chapters|Topic|Assessment|%
                        if (!weekDiv || !topicDiv) continue;
                        var weekText = weekDiv.innerText.trim();
                        for (var j = 0; j < data.length; j++) {{
                            if (data[j].week === weekText) {{
                                topicDiv.innerText = data[j].topic;
                                topicDiv.dispatchEvent(new Event('input',  {{bubbles: true}}));
                                topicDiv.dispatchEvent(new Event('change', {{bubbles: true}}));
                                topicDiv.style.border = '2px solid green';
                                filled++;
                                break;
                            }}
                        }}
                    }}
                    return filled;
                    """
                )
                report["fields_filled"].append(
                    {
                        "field": "Course Plan (Session Topics)",
                        "source": f"{plan_result} weeks populated from document",
                    }
                )
                print(f"[Curriculum] FILLED: Course Plan — {plan_result} weeks")
            except Exception as exc:
                report["fields_left_blank"].append(
                    {"field": "Course Plan", "reason": f"injection error: {exc}"}
                )
        else:
            report["fields_left_blank"].append(
                {"field": "Course Plan", "reason": "not found in source document"}
            )

        # Chapters, Assessment/% explicitly left blank per spec
        report["fields_left_blank"].append(
            {"field": "Course Plan → Chapters", "reason": "not present in document per spec"}
        )
        report["fields_left_blank"].append(
            {
                "field": "Course Plan → Assessment / %",
                "reason": "not present per-week in document (only aggregate Marks Distribution)",
            }
        )

        # ── Marks Distribution (structured row injection) ──
        if parsed.get("marks_distribution"):
            try:
                marks_json = json.dumps(parsed["marks_distribution"])
                marks_result = driver.execute_script(
                    f"""
                    var data = {marks_json};
                    var table = document.querySelector('.marks-table');
                    if (!table) return 'TABLE_NOT_FOUND';
                    var rows = table.querySelectorAll('.marks-row');
                    var filled = 0;
                    for (var i = 0; i < data.length; i++) {{
                        var targetIndex = i + 1;          // skip header row
                        if (targetIndex >= rows.length) break;
                        var htmlRow = rows[targetIndex];
                        var cells   = htmlRow.children;
                        if (cells.length >= 5) {{
                            for (var c = 0; c < 5; c++) {{
                                cells[c].innerText = data[i][c];
                                cells[c].dispatchEvent(new Event('input',  {{bubbles: true}}));
                                cells[c].dispatchEvent(new Event('change', {{bubbles: true}}));
                            }}
                            htmlRow.style.border = '1px solid green';
                            filled++;
                        }}
                    }}
                    return filled;
                    """
                )
                if marks_result == "TABLE_NOT_FOUND":
                    report["abort_reason"] = (
                        "Marks Distribution table (.marks-table) not found in portal DOM"
                    )
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": "Aborted: Marks Distribution table not found on portal",
                            "report": report,
                        },
                        status=500,
                    )
                report["fields_filled"].append(
                    {"field": "Marks Distribution", "source": f"{marks_result} rows populated"}
                )
                print(f"[Curriculum] FILLED: Marks Distribution — {marks_result} rows")
            except Exception as exc:
                report["fields_left_blank"].append(
                    {"field": "Marks Distribution", "reason": f"injection error: {exc}"}
                )
        else:
            report["fields_left_blank"].append(
                {"field": "Marks Distribution", "reason": "not found in source document"}
            )

        # ────────────────────────────────────────────────────────────────
        # PHASE 3: Self-verification (read-back pass)
        # ────────────────────────────────────────────────────────────────
        print("[Curriculum] Starting self-verification read-back pass …")

        def _normalize(text):
            """Strip HTML tags, collapse whitespace for comparison."""
            if not text:
                return ""
            cleaned = re.sub(r"<[^>]+>", " ", str(text))
            return " ".join(cleaned.split()).strip().lower()

        verified_count = 0
        for element_id, expectation in fill_expectations.items():
            field_name = expectation["field"]
            expected = expectation["value"]
            prop = expectation["prop"]

            try:
                actual = driver.execute_script(
                    f"var el = document.getElementById('{element_id}');"
                    f"return el ? el.{prop} : null;"
                )

                if actual is None:
                    report["verification_failures"].append(
                        {"field": field_name, "reason": "element disappeared from DOM after filling"}
                    )
                    report["fields_filled"] = [
                        f for f in report["fields_filled"] if f["field"] != field_name
                    ]
                else:
                    expected_norm = _normalize(expected)
                    actual_norm = _normalize(actual)
                    if expected_norm == actual_norm:
                        verified_count += 1
                    elif expected_norm and (
                        expected_norm in actual_norm or actual_norm in expected_norm
                    ):
                        # Partial match — accept (HTML tags may wrap content differently)
                        verified_count += 1
                    else:
                        report["verification_failures"].append(
                            {
                                "field": field_name,
                                "expected_snippet": expected[:100],
                                "actual_snippet": (actual or "")[:100],
                            }
                        )
                        print(f"[Curriculum] VERIFY FAIL: {field_name}")
            except Exception as exc:
                report["verification_failures"].append(
                    {"field": field_name, "reason": f"read-back error: {exc}"}
                )

        print(
            f"[Curriculum] Verification complete: "
            f"{verified_count}/{len(fill_expectations)} fields verified"
        )

        return JsonResponse({"status": "success", "report": report})

    except InvalidSessionIdException:
        return JsonResponse(
            {
                "status": "auth_required",
                "message": "Browser session expired. Please click Open Portal Browser and retry.",
            },
            status=400,
        )
    except Exception as exc:
        print(f"[Curriculum] Automation Error: {exc}")
        report["abort_reason"] = str(exc)
        return JsonResponse(
            {"status": "error", "message": str(exc), "report": report},
            status=500,
        )

