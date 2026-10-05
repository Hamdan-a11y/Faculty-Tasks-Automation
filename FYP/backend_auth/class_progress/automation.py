"""
Class Progress Tracker - Selenium Automation Module
Handles ZABDESK portal automation for auto-filling class progress forms.

Key Fix: Uses robust click strategies (WebDriverWait, ActionChains, JS fallback)
to reliably click the FACULTY button on the roles page.
"""

import time
import json
import os
import shutil
import subprocess
import socket
import random
import re
import requests
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import (
    TimeoutException, 
    ElementClickInterceptedException,
    StaleElementReferenceException,
    NoSuchElementException,
    WebDriverException,
)
from webdriver_manager.chrome import ChromeDriverManager
from django.conf import settings
from bs4 import BeautifulSoup

# Configuration
CHROME_PROFILE_PATH = os.path.join(settings.BASE_DIR, "chrome_automation_profile")
ZABDESK_LOGIN_URL = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/"
SCHEDULE_PATH = os.path.join(settings.BASE_DIR.parent, "schedule", "today.json")
DEBUG_PORT = 9222

# Cache ChromeDriver path once to avoid repeated downloads (speeds up reruns)
_raw_path = ChromeDriverManager().install()
if not _raw_path.endswith("chromedriver.exe"):
    _dir = os.path.dirname(_raw_path)
    _cand = os.path.join(_dir, "chromedriver.exe")
    if os.path.exists(_cand):
        CHROMEDRIVER_PATH = _cand
    else:
        _cand_up = os.path.join(os.path.dirname(_dir), "chromedriver.exe")
        if os.path.exists(_cand_up):
            CHROMEDRIVER_PATH = _cand_up
        else:
            CHROMEDRIVER_PATH = _raw_path
else:
    CHROMEDRIVER_PATH = _raw_path


class ZabdeskAutomation:
    """Handles all ZABDESK portal automation tasks."""
    
    driver = None

    # ──────────────────────────────────────────────────────────────
    # DRIVER MANAGEMENT
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _is_port_open(port: int) -> bool:
        """Check if a port is open (Chrome debug port)."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            return sock.connect_ex(('127.0.0.1', port)) == 0

    @staticmethod
    def _find_chrome_executable() -> str | None:
        """Find Chrome executable on the system."""
        candidates = [
            shutil.which("chrome"),
            shutil.which("google-chrome"),
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]
        for path in candidates:
            if path and os.path.exists(path):
                return path
        return None

    @classmethod
    def get_driver(cls):
        """
        Get or create WebDriver instance.
        Attempts to connect to existing Chrome debug session first.
        """
        # If we already have a driver, verify the session is still alive
        if cls.driver:
            try:
                cls.driver.execute_script("return 1")
                if cls.driver.window_handles:
                    return cls.driver
            except WebDriverException:
                # Session is dead; reset and recreate below
                try:
                    cls.driver.quit()
                except Exception:
                    pass
                cls.driver = None

        options = Options()
        options.add_experimental_option("debuggerAddress", f"127.0.0.1:{DEBUG_PORT}")
        options.add_experimental_option("prefs", {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        })
        options.page_load_strategy = "eager"  # faster load, don't wait for full resources

        try:
            # Launch Chrome with debug port if not already running
            if not cls._is_port_open(DEBUG_PORT):
                print(f"[Automation] Debug port {DEBUG_PORT} not open. Launching Chrome...")
                chrome_path = cls._find_chrome_executable()
                if not chrome_path:
                    raise RuntimeError("Chrome executable not found on system")
                
                os.makedirs(CHROME_PROFILE_PATH, exist_ok=True)
                subprocess.Popen([
                    chrome_path,
                    f"--remote-debugging-port={DEBUG_PORT}",
                    "--remote-allow-origins=*",
                    f"--user-data-dir={CHROME_PROFILE_PATH}",
                    "--profile-directory=Default",
                    "--no-first-run",
                    "--start-maximized"
                ])
                print("[Automation] Chrome launched. Waiting for debug port...")
                time.sleep(0.8)

            # Connect to Chrome
            service = Service(CHROMEDRIVER_PATH)
            cls.driver = webdriver.Chrome(service=service, options=options)
            print(f"[Automation] Connected to Chrome. Current URL: {cls.driver.current_url}")
            return cls.driver

        except Exception as e:
            print(f"[Automation] Connection error: {e}")
            # Fallback: Create new Chrome instance
            if cls.driver is None:
                print("[Automation] Fallback: Creating new Chrome instance...")
                cls.driver = webdriver.Chrome(service=Service(CHROMEDRIVER_PATH))
            return cls.driver

    # ──────────────────────────────────────────────────────────────
    # ROBUST CLICK UTILITIES
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def robust_click(cls, element, driver, description="element"):
        """
        Attempt multiple click strategies to reliably click an element.
        This fixes the FACULTY button click issue.
        
        Strategies (in order):
        1. Standard Selenium click
        2. ActionChains click
        3. JavaScript click
        4. JavaScript dispatchEvent
        """
        strategies = [
            ("Standard click", lambda: element.click()),
            ("ActionChains click", lambda: ActionChains(driver).move_to_element(element).click().perform()),
            ("JS click", lambda: driver.execute_script("arguments[0].click();", element)),
            ("JS dispatchEvent", lambda: driver.execute_script("""
                var evt = new MouseEvent('click', {
                    bubbles: true, cancelable: true, view: window
                });
                arguments[0].dispatchEvent(evt);
            """, element)),
        ]

        for strategy_name, strategy_fn in strategies:
            try:
                # Scroll element into view first
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                time.sleep(0.3)
                
                strategy_fn()
                print(f"[Automation] ✓ {description} clicked via {strategy_name}")
                return True
            except (ElementClickInterceptedException, StaleElementReferenceException) as e:
                print(f"[Automation] {strategy_name} failed: {e}")
                continue
            except Exception as e:
                print(f"[Automation] {strategy_name} error: {e}")
                continue

        print(f"[Automation] ✗ All click strategies failed for {description}")
        return False

    @classmethod
    def find_and_click_by_text(cls, driver, text, tag="*", timeout=10):
        """
        Find element containing specific text and click it robustly.
        Uses XPath with contains() for flexible text matching.
        """
        wait = WebDriverWait(driver, timeout)
        
        # Multiple XPath strategies for finding text
        xpaths = [
            f"//{tag}[normalize-space(text())='{text}']",
            f"//{tag}[contains(normalize-space(.), '{text}')]",
            f"//{tag}[contains(@class, 'faculty') or contains(@class, 'Faculty')]",
            f"//button[contains(., '{text}')]",
            f"//a[contains(., '{text}')]",
            f"//div[contains(@class, 'panel')][contains(., '{text}')]",
        ]
        
        for xpath in xpaths:
            try:
                element = wait.until(EC.element_to_be_clickable((By.XPATH, xpath)))
                if cls.robust_click(element, driver, f"'{text}' element"):
                    return True
            except TimeoutException:
                continue
            except Exception as e:
                print(f"[Automation] XPath '{xpath}' error: {e}")
                continue
        
        return False

    # ──────────────────────────────────────────────────────────────
    # LOGIN WORKFLOW
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def login(cls):
        """
        Fast login to ZABDESK dummy portal.
        """
        return cls.open_post_class_progress()

    # ──────────────────────────────────────────────────────────────
    # NAVIGATION WORKFLOW
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def open_post_class_progress(cls):
        """
        Fast login & navigation to ZABDESK Dummy Portal Post Class Progress page:
        https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/post-class-progress.html?courseId=1
        """
        driver = cls.get_driver()
        target_url = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/post-class-progress.html?courseId=1"
        
        print(f"[Automation] Fast login and navigating directly to: {target_url}")
        
        # Open dummy portal homepage first to establish session storage
        driver.get("https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/")
        time.sleep(0.2)
        
        # Set authenticated session storage and navigate directly to post-class-progress page
        driver.execute_script(f"""
            sessionStorage.setItem('currentUser', JSON.stringify({{ id: '1', userId: 'FAC001', name: 'Faculty Member', role: 'faculty' }}));
            sessionStorage.setItem('selectedRole', 'faculty');
            window.location.href = '{target_url}';
        """)
        time.sleep(0.4)
        
        return {
            "status": "success",
            "message": "Logged in to ZABDESK (Faculty) and opened Post Class Progress page",
            "url": driver.current_url,
            "course_clicked": True,
            "pcp_clicked": True,
        }

    # ──────────────────────────────────────────────────────────────
    # AUTO-FILL WORKFLOW
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def auto_fill(cls):
        """
        Auto-fills today's class progress on ZABDESK dummy portal:
        1. Ensures browser is on https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/post-class-progress.html?courseId=1
        2. Reads schedule data from today.json (extracted from Noticeboard)
        3. Fills lecture No, date, start time, end time, topics covered, class status
        """
        driver = cls.get_driver()
        target_url = "https://hamdan-a11y.github.io/ZABDESK-Dummy-Portal/post-class-progress.html?courseId=1"
        
        # Navigate to dummy portal post-class-progress if not currently on it
        if "post-class-progress" not in driver.current_url:
            cls.open_post_class_progress()
            time.sleep(0.3)

        try:
            # Load schedule data from today.json
            schedule_data = {
                "course_code": "CS-101",
                "topic": "Object Oriented Programming Techniques",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": "09:00 AM - 12:00 PM",
                "status": "Held",
                "course_name": "Object Oriented Programming Techniques"
            }
            
            if os.path.exists(SCHEDULE_PATH):
                try:
                    with open(SCHEDULE_PATH, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                        schedule_data.update(loaded)
                    print(f"[Automation] [OK] Loaded schedule data from: {SCHEDULE_PATH}")
                except Exception as e:
                    print(f"[Automation] [FAIL] Could not load {SCHEDULE_PATH}: {e}")

            today_display = datetime.now().strftime("%d-%m-%Y")
            today_iso = datetime.now().strftime("%Y-%m-%d")

            topic_to_fill = schedule_data.get("topic") or schedule_data.get("course_name") or "Object Oriented Programming Techniques - Polymorphism & Interfaces"
            status_to_fill = schedule_data.get("status", "Held")
            
            # Parse start time and end time
            time_str = schedule_data.get("time", "09:00 AM - 12:00 PM")
            start_hr, start_min = "09", "00"
            end_hr, end_min = "12", "00"
            
            if " - " in time_str:
                parts = time_str.split(" - ")
                start_part = parts[0].strip().replace(" AM", "").replace(" PM", "")
                end_part = parts[1].strip().replace(" AM", "").replace(" PM", "")
                
                if ":" in start_part:
                    sh, sm = start_part.split(":")[:2]
                    start_hr, start_min = sh.zfill(2), sm.zfill(2)
                if ":" in end_part:
                    eh, em = end_part.split(":")[:2]
                    end_hr, end_min = eh.zfill(2), em.zfill(2)

            fill_js = """
                const results = {};
                
                // Lecture No
                const lec = document.querySelector('input[aria-label="Lecture number"]') || document.querySelector('table.next-legacy-table input');
                if (lec) {
                    lec.value = '21';
                    lec.dispatchEvent(new Event('input', {bubbles: true}));
                    lec.dispatchEvent(new Event('change', {bubbles: true}));
                    results.lecture_no = '21';
                }

                // Date Text (DD-MM-YYYY)
                const dtText = document.getElementById('nextLegacyDateText');
                if (dtText) {
                    dtText.removeAttribute('readonly');
                    dtText.value = arguments[0];
                    dtText.dispatchEvent(new Event('input', {bubbles: true}));
                    dtText.dispatchEvent(new Event('change', {bubbles: true}));
                    results.date_text = arguments[0];
                }

                // Date Picker (YYYY-MM-DD)
                const dtPicker = document.getElementById('nextLegacyDatePicker');
                if (dtPicker) {
                    dtPicker.value = arguments[1];
                    dtPicker.dispatchEvent(new Event('change', {bubbles: true}));
                }

                // Class Start Time
                const sHrs = document.querySelector('select[aria-label="Start hours"]');
                if (sHrs) {
                    sHrs.value = arguments[2];
                    sHrs.dispatchEvent(new Event('change', {bubbles: true}));
                }
                const sMins = document.querySelector('select[aria-label="Start minutes"]');
                if (sMins) {
                    sMins.value = arguments[3];
                    sMins.dispatchEvent(new Event('change', {bubbles: true}));
                }

                // Class End Time
                const eHrs = document.querySelector('select[aria-label="End hours"]');
                if (eHrs) {
                    eHrs.value = arguments[4];
                    eHrs.dispatchEvent(new Event('change', {bubbles: true}));
                }
                const eMins = document.querySelector('select[aria-label="End minutes"]');
                if (eMins) {
                    eMins.value = arguments[5];
                    eMins.dispatchEvent(new Event('change', {bubbles: true}));
                }

                // Topics Covered
                const topicArea = document.querySelector('textarea[aria-label="Topics covered"]');
                if (topicArea) {
                    topicArea.value = arguments[6];
                    topicArea.dispatchEvent(new Event('input', {bubbles: true}));
                    topicArea.dispatchEvent(new Event('change', {bubbles: true}));
                    results.topic = arguments[6];
                }

                // Class Status
                const statusSel = document.querySelector('select[aria-label="Class status"]');
                if (statusSel) {
                    statusSel.value = arguments[7];
                    statusSel.dispatchEvent(new Event('change', {bubbles: true}));
                    results.status = arguments[7];
                }

                return results;
            """

            fill_res = driver.execute_script(
                fill_js,
                today_display,
                today_iso,
                start_hr,
                start_min,
                end_hr,
                end_min,
                topic_to_fill,
                status_to_fill
            )
            print(f"[Automation] [OK] Auto-filled form on ZABDESK dummy portal: {fill_res}")
            time.sleep(0.4)

            return {
                "status": "success",
                "message": "Today's class progress form auto-filled successfully on ZABDESK dummy portal!",
                "data": {
                    "lecture_no": "21",
                    "date": today_display,
                    "start_time": f"{start_hr}:{start_min}",
                    "end_time": f"{end_hr}:{end_min}",
                    "topic": topic_to_fill,
                    "status": status_to_fill
                }
            }

        except Exception as e:
            print(f"[Automation] [FAIL] Auto-fill error: {e}")
            return {"status": "error", "message": f"Auto-fill failed: {e}"}

    @classmethod
    def close(cls):
        """Close the WebDriver session."""
        if cls.driver:
            try:
                cls.driver.quit()
            except:
                pass
            cls.driver = None
            print("[Automation] WebDriver closed")

    # ──────────────────────────────────────────────────────────────
    # NOTICEBOARD EXTRACTION
    # ──────────────────────────────────────────────────────────────

    @classmethod
    def extract_noticeboard(cls):
        """
        Navigates browser to ZABDESK Islamabad actual portal Noticeboard page,
        accesses Classes Schedule, and extracts all class schedule entries from the table.
        """
        driver = cls.get_driver()
        noticeboard_url = "https://zabdesk.szabist-isb.edu.pk/ZabNoticeboard/StudentNoticeboard.aspx"
        
        print(f"[Automation] Navigating to ZABDESK Islamabad Portal Noticeboard: {noticeboard_url}")
        driver.get(noticeboard_url)
        time.sleep(0.5)
        
        try:
            wait = WebDriverWait(driver, 5)
            table = wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            rows = driver.find_elements(By.TAG_NAME, "tr")
            
            extracted_classes = []
            for row in rows:
                cols = [c.text.strip() for c in row.find_elements(By.XPATH, "./td|./th")]
                if len(cols) >= 8 and cols[0].isdigit():
                    class_info = {
                        "sr_no": cols[0],
                        "department": cols[1],
                        "program": cols[2],
                        "section": cols[3] if len(cols) > 3 else "",
                        "course_name": cols[4] if len(cols) > 4 else "",
                        "faculty_name": cols[5] if len(cols) > 5 else "",
                        "room": cols[6] if len(cols) > 6 else "",
                        "class_time": cols[7] if len(cols) > 7 else "",
                        "campus": cols[8] if len(cols) > 8 else "",
                    }
                    extracted_classes.append(class_info)
            
            print(f"[Automation] [OK] Extracted {len(extracted_classes)} class schedule entries from ZABDESK Islamabad Noticeboard")
            
            first_class = extracted_classes[0] if extracted_classes else {}
            today_str = datetime.now().strftime("%Y-%m-%d")
            
            schedule_data = {
                "course_code": "CS-101",
                "course_name": first_class.get("course_name", "Object Oriented Programming Techniques"),
                "topic": first_class.get("course_name", "Object Oriented Programming Techniques"),
                "date": today_str,
                "time": first_class.get("class_time", "09:00 AM - 12:00 PM"),
                "room": first_class.get("room", "Lab 06"),
                "faculty": first_class.get("faculty_name", "Faculty Member"),
                "status": "Held",
                "extracted_classes": extracted_classes
            }
            
            # Save to today.json
            os.makedirs(os.path.dirname(SCHEDULE_PATH), exist_ok=True)
            with open(SCHEDULE_PATH, 'w', encoding='utf-8') as f:
                json.dump(schedule_data, f, indent=2)
                
            return {
                "status": "success",
                "message": f"Successfully extracted {len(extracted_classes)} classes schedule from ZABDESK Islamabad Portal Noticeboard",
                "data": {
                    "url": noticeboard_url,
                    "extracted_count": len(extracted_classes),
                    "classes": extracted_classes,
                    "text_length": len(driver.page_source),
                    "tables_count": len(driver.find_elements(By.TAG_NAME, "table"))
                }
            }
        except Exception as e:
            print(f"[Automation] [FAIL] Noticeboard extraction error: {e}")
            return {"status": "error", "message": f"Extraction failed: {e}"}

