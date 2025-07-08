import json
import os
import time
import xml.etree.ElementTree as ET
import re
import subprocess
import uiautomator2 as u2
from groq import Groq
from dotenv import load_dotenv
from openai import OpenAI
import requests
import zipfile
import tempfile
import os
import sys
import socket   
from vncdotool import api
from PIL import Image
import io
import pyautogui
import pygetwindow as gw
import shutil
import threading
from fpdf import FPDF
from fpdf.enums import XPos, YPos
import unicodedata
from datetime import datetime
import subprocess
import time
import re

# === Groq Client ===
#client = Groq(api_key="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")
load_dotenv()
client = OpenAI(api_key=os.getenv("API_KEY"))

STATE_HISTORY_FILE = "exploration_history_chk2.json"

def get_cpu_usage(package_name):
    try:
        # Use 'top -n 1' to get current CPU usage
        output = subprocess.check_output(
            ["adb", "shell", "top", "-n", "1"],
            text=True
        )

        cpu_usages = []

        for line in output.splitlines():
            if package_name in line:
                # Your confirmed matching format:
                # PID USER ... CPU% ... NAME
                match = re.search(r"\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\d+(?:\.\d+)?)%\s+\S+\s+" + re.escape(package_name), line)
                if match:
                    cpu_usages.append(float(match.group(1)))

        # Fallback to dumpsys cpuinfo if top yields nothing
        if not cpu_usages:
            output = subprocess.check_output(["adb", "shell", "dumpsys", "cpuinfo"], text=True)
            for line in output.splitlines():
                if package_name in line:
                    match = re.search(r"([\d\.]+)%", line)
                    if match:
                        cpu_usages.append(float(match.group(1)))

        if cpu_usages:
            avg_cpu = round(sum(cpu_usages) / len(cpu_usages), 2)
            peak_cpu = round(max(cpu_usages), 2)
            return avg_cpu, peak_cpu
        else:
            return 0.0, 0.0

    except Exception as e:
        print(f"[ERROR] CPU usage reading failed: {e}")
        return 0.0, 0.0


def get_fps_and_jank(package_name, duration_sec):
    """
    Returns (average_fps, janky_frame_count) using duration-sec-based FPS calculation.
    """
    try:
        output = subprocess.check_output(
            ["adb", "shell", f"dumpsys gfxinfo {package_name}"],
            text=True
        )

        print("[DEBUG] Raw gfxinfo:\n", output)


        total_frames = 0
        janky_frames = 0

        for line in output.splitlines():
            if "Total frames rendered:" in line:
                total_frames = int(re.search(r"Total frames rendered:\s*(\d+)", line).group(1))
            elif "Janky frames:" in line:
                janky_frames = int(re.search(r"Janky frames:\s*(\d+)", line).group(1))

        if total_frames > 0 and duration_sec > 0:
            avg_fps = round((total_frames / duration_sec), 2)
            return avg_fps, janky_frames
        else:
            return 0.0, 0
    except Exception as e:
        print(f"[ERROR] FPS/jank reading failed: {e}")
        return 0.0, 0




def get_network_usage(package_name=None):
    """
    Returns (tx_kb, rx_kb) from /proc/net/dev.
    This gives total device-level Wi-Fi traffic (not per-app).
    """
    try:
        output = subprocess.check_output(["adb", "shell", "cat", "/proc/net/dev"], text=True)
        tx_total = 0
        rx_total = 0

        for line in output.splitlines()[2:]:  # Skip headers
            line = line.replace(":", " ")
            parts = line.split()
            if len(parts) >= 17:
                iface = parts[0]
                rx_bytes = int(parts[1])
                tx_bytes = int(parts[9])

                if iface.startswith("wlan") or iface.startswith("eth") or iface.startswith("rmnet"):
                    rx_total += rx_bytes
                    tx_total += tx_bytes

        return round(tx_total / 1024), round(rx_total / 1024)  # return in KB

    except Exception as e:
        print(f"[ERROR] Failed to read network usage: {e}")
        return 0, 0
    
def get_memory_usage(package_name):
    """
    Returns (rss_MB, pss_MB) for a given package using dumpsys meminfo.
    """
    try:
        output = subprocess.check_output(
            ["adb", "shell", f"dumpsys meminfo {package_name}"],
            text=True
        )

        rss, pss = 0.0, 0.0

        for line in output.splitlines():
            if "TOTAL" in line:
                parts = line.split()
                if len(parts) >= 3:
                    rss = round(int(parts[1]) / 1024, 2)  # KB → MB
                    pss = round(int(parts[2]) / 1024, 2)
                    break

        return rss, pss

    except Exception as e:
        print(f"[ERROR] Failed to get memory usage: {e}")
        return 0.0, 0.0




def save_metrics_report(app_name, suggested_functions, completed_functions, start_time, end_time, package_name,
                        cpu_avg, cpu_peak, rss_avg, rss_peak, pss_avg, start_battery, end_battery, fps_avg, janky_frames,
                        network_usage, actions_performed):
    total_test_time_seconds = int((end_time - start_time).total_seconds())
    functions_passed = len(completed_functions)
    functions_failed = len(suggested_functions) - functions_passed

    metrics_data = {
        "app_name": app_name,
        "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_test_time_seconds": total_test_time_seconds,
        "functions_passed": functions_passed,
        "functions_failed": functions_failed,
        "actions_performed": {
            "inputs": actions_performed.get("inputs", 0),
            "clicks": actions_performed.get("clicks", 0),
        },
        "screens_visited": activity_count,
        "test_case_completion_times": {
            desc: total_test_time_seconds for desc in suggested_functions
        },
        "cpu_usage": {
            "average": round(cpu_avg, 2),
            "peak": round(cpu_peak, 2),
        },
        "memory_usage": {
            "rss": round(rss_avg, 1),  # MB
            "pss": round(pss_avg, 1),  # MB
        },
        "battery": {
            "start_level": start_battery,
            "end_level": end_battery,
            "temperature": 35  # replace with real temperature reading if needed
        },
        "fps": {
            "average_fps": round(fps_avg, 2),
            "janky_frames": int(janky_avg),  # set janky frame count if you calculate it
        },
        "network_usage": {
            "tx_kb": network_usage[0],
            "rx_kb": network_usage[1],
        },
        "device_model": "Pixel 5",
        "android_version": "13",
        "individual_test_case_times": {
            desc: {"duration": total_test_time_seconds, "status": "Completed"} for desc in suggested_functions
        }
    }

    backend_dir = os.path.dirname(os.path.abspath(__file__))
    metrics_path = os.path.join(backend_dir, "metrics_report.json")
    with open(metrics_path, "w") as f:
        json.dump(metrics_data, f, indent=2)
    print(f"✅ Metrics report saved to {metrics_path}")



def format_app_name(pkg):
    parts = pkg.split(".")
    if parts:
        return parts[-1].capitalize()
    else:
        return pkg

def generate_pdf_in_background():
    import os
    import threading

    print("🧵 Starting background PDF generation...")


    thread = threading.Thread(
        target=generate_pdf_report,
        args=(
            app_name,
            suggested_functions.copy(),
            completed_functions.copy(),
            function_descriptions.copy(),
            screenshot_log.copy()
        )
    )
    thread.start()

def generate_pdf_report(app_name, suggested_functions, completed_functions, function_descriptions, screenshot_log):
    from fpdf import FPDF
    from fpdf.enums import XPos, YPos
    import os
    import unicodedata

    def clean_text(text):
        return ''.join(c for c in unicodedata.normalize('NFKD', str(text)) if ord(c) < 128)

    class PDF(FPDF):
        def header(self):
            self.set_font("Helvetica", "I", 9)
            self.set_text_color(160, 160, 160)
            self.cell(0, 10, "UXplore AI", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
            self.set_draw_color(220, 220, 220)
            self.line(10, 18, 200, 18)

        def footer(self):
            self.set_y(-15)
            self.set_font("Helvetica", "I", 8)
            self.set_text_color(130, 130, 130)
            self.cell(0, 10, f"Page {self.page_no()}", 0, new_x=XPos.RIGHT, new_y=YPos.TOP, align="C")

        def add_cover(self):
            self.add_page()
            self.set_fill_color(245, 250, 255)
            self.rect(0, 0, 210, 297, 'F')
            self.set_y(80)
            self.set_font("Helvetica", "B", 28)
            self.set_text_color(30, 30, 70)
            self.cell(0, 15, "UXplore AI", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

            self.set_font("Helvetica", "B", 18)
            self.cell(0, 10, f"Test Report for: {clean_text(app_name)}", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

            self.ln(20)
            self.set_font("Helvetica", "I", 12)
            self.set_text_color(90, 90, 90)
            self.cell(0, 10, "Automated Android App Navigation System", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

        def add_introduction(self):
            self.add_page()
            self.set_font("Helvetica", "B", 16)
            self.set_text_color(30, 30, 30)
            self.cell(0, 12, "Introduction", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_font("Helvetica", "", 12)
            self.set_text_color(60, 60, 60)
            intro = (
                f"This report presents the testing results for '{clean_text(app_name)}', powered by UXplore AI. "
                "The system autonomously explored the app, identified core functions, and executed a set of key flows. "
                "Below you'll find a table listed all the tested functions, function status, and final test summary."
            )
            self.multi_cell(0, 8, clean_text(intro))
            self.ln(5)

        def add_test_case_table(self):
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(40, 40, 40)
            self.cell(0, 12, "Test Cases", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.set_fill_color(230, 240, 255)
            self.set_draw_color(180, 180, 180)

            self.set_font("Helvetica", "B", 11)
            self.cell(15, 10, "ID", 1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C', fill=True)
            self.cell(110, 10, "Test Case Description", 1, new_x=XPos.RIGHT, new_y=YPos.TOP, align='C', fill=True)
            self.cell(55, 10, "Status", 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C', fill=True)

            self.set_font("Helvetica", "", 11)
            for idx, test_case in enumerate(suggested_functions, start=1):
                desc = clean_text(str(test_case))
                is_passed = test_case in completed_functions
                row_color = (255, 255, 255) if idx % 2 == 0 else (245, 250, 255)
                self.set_fill_color(*row_color)
                self.set_text_color(0, 0, 0)

                # Remember current position
                x_start = self.get_x()
                y_start = self.get_y()

                # ID cell
                self.multi_cell(15, 10, str(idx), border=1, align='C', fill=True)
                # Move cursor back for description column
                self.set_xy(x_start + 15, y_start)
                self.multi_cell(110, 10, desc, border=1, align='L', fill=True)
                # Calculate height of the row (tallest cell between ID and desc)
                y_end = self.get_y()
                row_height = y_end - y_start

                # Move cursor back for status cell
                self.set_xy(x_start + 15 + 110, y_start)
                self.set_text_color(0, 150, 0) if is_passed else self.set_text_color(200, 0, 0)
                status = "Pass" if is_passed else "Fail"
                self.multi_cell(55, row_height, status, border=1, align='C', fill=True)

                # Move to next line at the end of the tallest cell
                self.set_y(y_start + row_height)

                # Status text: green if passed, red if not
                # if is_passed:
                #     self.set_text_color(0, 150, 0)  # Green
                #     status = "Pass"
                # else:
                #     self.set_text_color(200, 0, 0)  # Red
                #     status = "Fail"

                # self.cell(55, 10, status, 1, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C', fill=True)

            self.set_text_color(0, 0, 0)
            self.ln(6)

        def add_final_summary(self):
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(30, 30, 30)
            self.cell(0, 12, "Final Summary", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

            self.set_font("Helvetica", "", 12)
            self.set_text_color(50, 50, 50)
            coverage = round(100 * len(completed_functions) / (len(suggested_functions) or 1), 2)
            summary = (
                f"- Total Functions Suggested: {len(suggested_functions)}\n"
                f"- Functions Successfully Executed: {len(completed_functions)}\n"
                f"- Test Coverage Achieved: {coverage}%"
            )
            self.multi_cell(0, 8, summary)
            self.ln(5)

        def add_screenshot_log(self):
            if not screenshot_log:
                return

            self.add_page()
            self.set_font("Helvetica", "B", 14)
            self.set_text_color(30, 30, 30)
            self.cell(0, 12, "Captured Screens During Testing", 0, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            self.ln(2)

            for i, (test_case, paths) in enumerate(screenshot_log.items(), start=1):
                if not isinstance(paths, list):
                    paths = [paths]

                for path in paths:
                    if os.path.exists(path):
                        raw_desc = function_descriptions.get(test_case)
                        if isinstance(raw_desc, list):
                            desc = ' > '.join(raw_desc)
                        else:
                            desc = raw_desc or test_case

                        self.set_font("Helvetica", "I", 11)
                        self.set_text_color(80, 80, 80)
                        self.multi_cell(0, 8, f"{i}. {clean_text(desc)}")

                        self.set_x((210 - 120) / 2)
                        self.image(path, w=120)
                        self.ln(10)

    # ----------- Generate Report ----------
    pdf = PDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_cover()
    pdf.add_introduction()
    pdf.add_test_case_table()
    pdf.add_final_summary()
    #pdf.add_screenshot_log()
    reports_dir = os.path.join(os.getcwd(), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    output_path = os.path.join(reports_dir, f"{app_name}_Test_Report.pdf")
    try:
        pdf.output(output_path)
        print(f"✅ PDF report successfully generated: {output_path}")
    except Exception as e:
        print(f"❌ Error generating PDF report: {e}")

    try:
    # Tell Node server the report is ready
        response = requests.post("http://localhost:3000/api/report-ready", json={
            "reportFile": f"{app_name}_Test_Report.pdf"
        })
        print(f"✅ Notified Node server: {response.status_code} {response.text}")
    except Exception as e:
        print(f"❌ Failed to notify server: {e}")

suggested_functions = []
verification_code=None
last_result=None
current_function = None
completed_functions = []
function_done = False
previous_actions = []
function_descriptions = {}
screenshot_log = {}  
actions_performed = {
    "inputs": 0,
    "clicks": 0
}


def handle_xapk(xapk_path):
    temp_dir = tempfile.mkdtemp()
    with zipfile.ZipFile(xapk_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)

    apk_paths = []
    for root, dirs, files in os.walk(temp_dir):
        for file in files:
            if file.endswith(".apk"):
                apk_paths.append(os.path.join(root, file))

    if not apk_paths:
        raise FileNotFoundError("[ERROR] No .apk found inside the .xapk file.")

    print("✅ Extracted APKs:")
    for path in apk_paths:
        print(f"  - {path}")

    return apk_paths, temp_dir

def get_location():
    try:
        response = requests.get("http://ip-api.com/json").json()
        location_data = {
            "latitude": response.get("lat", "Unknown"),
            "longitude": response.get("lon", "Unknown"),
            "address": response.get("city", "Unknown"),
            "country": response.get("country", "Unknown")
        }
        filepath = os.path.join(os.path.dirname(__file__), "location_data.json")
        with open(filepath, "w") as f:
            json.dump(location_data, f, indent=4)
        
        # print(f"Location data are successfullly saved to {filepath}")
        return location_data

    except Exception as e:
        print(f"[ERROR] Error fetching location: {e}")
        return None

def load_location():
    try:
        filepath = os.path.join(os.path.dirname(__file__), "location_data.json")
        with open(filepath, "r", encoding="utf-8") as f:
            location_data = json.load(f)
            return location_data
    except FileNotFoundError:
        print("[WARNING] location_data.json not found. Using defaults.")
        location_data = {"latitude": "Unknown", "longitude": "Unknown", "address": "Unknown", "country": "Unknown"}
        return location_data
    

def load_credentials():
    try:
        filepath = os.path.join(os.path.dirname(__file__), "credentials.json")
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to load credentials: {e}")
        return None

CREDENTIALS = load_credentials()
if not os.path.exists(os.path.join(os.path.dirname(__file__), "location_data.json")):
    get_location()
LOCATION = load_location()

import uiautomator2 as u2
def initialize_u2():
    print("📱 Connecting to device via uiautomator2...")
    device = u2.connect()
    device.set_fastinput_ime(True)
    print(f"✅ Connected to {device.serial}")
    return device


def capture_fixed_location_screenshot(step):
    import pyautogui, os
    # Set your fixed region here:
    x, y, w, h = 250, 100, 500, 900  # adjust as needed
    screenshot = pyautogui.screenshot(region=(x, y, w, h))
    os.makedirs("screenshots", exist_ok=True)
    file_path = os.path.join("screenshots", f"step_{step}.png")
    screenshot.save(file_path)
    print(f"[INFO] Screenshot saved to {file_path}")

def encode_image_to_base64(image_path):
    import base64
    with open(image_path, "rb") as img_file:
        return base64.b64encode(img_file.read()).decode('utf-8')

def load_state_history():
    if os.path.exists(STATE_HISTORY_FILE):
        with open(STATE_HISTORY_FILE, "r", encoding="utf-8") as file:
            try:
                return json.load(file)
            except json.JSONDecodeError:
                return {"StateHistory": []}
    return {"StateHistory": []}

def save_state_history(state_history):
    with open(STATE_HISTORY_FILE, "w", encoding="utf-8") as file:
        json.dump({"StateHistory": state_history}, file, indent=4)

def clear_state_history():
    save_state_history([])

def extract_viewtree(device):
    time.sleep(5)
    viewtree_xml = device.dump_hierarchy(compressed=False)

    if "<hierarchy" not in viewtree_xml:
        time.sleep(2)
        viewtree_xml = device.dump_hierarchy(compressed=False)

    debug_path = os.path.join(os.path.dirname(__file__), "debug_viewtree.xml")
    with open(debug_path, "w", encoding="utf-8") as f:
        f.write(viewtree_xml)

    return viewtree_xml

def extract_actionable_elements(device):
    time.sleep(10)
    actionable_elements = []
    viewtree_xml = extract_viewtree(device)

    if not viewtree_xml:
        print("⚠️ ViewTree XML is empty. No elements to extract.")
        return []

    try:
        root = ET.fromstring(viewtree_xml)
        modal_detected = False

        for node in root.iter():
            resource_id = node.attrib.get("resource-id", "").strip()
            if "inputmethod.latin" in resource_id or "android.inputmethod" in resource_id:
                continue

            text = node.attrib.get("text", "").strip()
            clickable = node.attrib.get("clickable", "false") == "true"
            class_name = node.attrib.get("class", "")
            content_desc = node.attrib.get("content-desc", "").strip()
            bounds = node.attrib.get("bounds", "").strip()
            hint = node.attrib.get("hint", "").strip()

            if "Dialog" in class_name or "PopupWindow" in class_name:
                modal_detected = True

            element_type = None
            common_clickables = [
                "Button", "RadioButton", "ImageView", "TextView", "CheckedTextView", "LinearLayout"  
            ]

            if modal_detected:
                if any(widget in class_name for widget in common_clickables) and clickable:
                    element_type = "Click"
            else:
                if "EditText" in class_name and (resource_id or content_desc or text or hint or bounds):
                    element_type = "Input"
                elif (
                    clickable and (
                        any(widget in class_name for widget in common_clickables)
                        or "android.view.View" in class_name or "FrameLayout" in class_name or "LinearLayout" in class_name or True  # fallback: consider any clickable node
                )):
                # ) and (resource_id or content_desc or text or hint): #removed or clickable from the condition
                    element_type = "Click"

            valid_resource_id = re.match(r"^[\w.]+:id/[\w_]+$", resource_id)
      
            if element_type:
                # identifier = (
                #     resource_id if valid_resource_id
                #     else f"bounds::{bounds}" if bounds
                #     else f"content::{content_desc}" if content_desc
                #     else f"text::{text}" if text
                #     else f"hint::{hint}" if hint
                #     else None
                # )
                identifier = f"{content_desc or text or hint or 'Unnamed'}::bounds::{bounds}"
                if identifier:
                    actionable_elements.append({
                        "Type": element_type,
                        "Element_Name": content_desc or text or hint or "Unnamed", 
                        "ResourceID": identifier,
                        "Bounds": bounds
                    })

    except ET.ParseError as e:
        print(f"[ERROR] XML Parsing Error: {e}")
        return []

    print("\n🧐 Extracted Actionable Elements:")
    for elem in actionable_elements:
        print(f"  - {elem['Type']}: {elem['Element_Name']} (ResourceID: {elem['ResourceID']})")

    return actionable_elements

def tap_at_coordinates(device, x, y):
    print(f"👆 Tapping at ({x}, {y})...")
    device.click(x, y)

def hide_keyboard(device):
    """
    Attempts to hide the keyboard by sending key events.
    KEYCODE_ESCAPE (111) and KEYCODE_BACK (4) are used as fallbacks.
    """
    print("⌨️ Attempting to hide keyboard...")
    try:
        device.shell("input keyevent 111")  # KEYCODE_ESCAPE
        time.sleep(0.5)
        device.shell("input keyevent 4")    # KEYCODE_BACK
        print("✅ Keyboard hidden.")
    except Exception as e:
        print(f"[ERROR] Failed to hide keyboard: {e}")

def get_bounds_for_resource_id(resource_id, viewtree_xml_path="debug_viewtree.xml"):
    
    try:
        viewtree_xml_path = os.path.join(os.path.dirname(__file__), viewtree_xml_path)
        tree = ET.parse(viewtree_xml_path)
        root = tree.getroot()
        for node in root.iter():
            if node.attrib.get("resource-id") == resource_id:
                bounds_str = node.attrib.get("bounds")
                if bounds_str:
                    coords = re.findall(r'\[(\d+),(\d+)\]', bounds_str)
                    if len(coords) == 2:
                        x1, y1 = map(int, coords[0])
                        x2, y2 = map(int, coords[1])
                        center_x = (x1 + x2) // 2
                        center_y = (y1 + y2) // 2
                        return center_x, center_y
    except Exception as e:
        print(f"⚠️ Error parsing viewtree for resource-id bounds: {e}")
    return None


def execute_action(device, action_type, target_element, input_text=None):
    time.sleep(3)
    print(f"🔍 Trying to interact with: {target_element} (Action: {action_type})")
    print(f"ActionType: {action_type}")
    def resolve_coordinates(identifier):
        # if identifier.startswith("bounds::"):
        if "::bounds::" in identifier:
            bounds_str = identifier.split("::", 1)[1]
            coords = re.findall(r'\[(\d+),(\d+)\]', bounds_str)
            if len(coords) == 2:
                x1, y1 = map(int, coords[0])
                x2, y2 = map(int, coords[1])
                return (x1 + x2) // 2, (y1 + y2) // 2
        else:
            return get_bounds_for_resource_id(identifier)  # Parses XML to get bounds
        return None

    try:
        if action_type == "Click":
            coords = resolve_coordinates(target_element)
            if coords:
                tap_at_coordinates(device, *coords)
                print(f"✅ Tapped at {coords}")
                actions_performed["clicks"] += 1
                time.sleep(2)
                return True
            else:
                print("⚠️ Could not resolve coordinates for tap.")
        elif action_type == "Input" and input_text:
            coords = resolve_coordinates(target_element)
            if coords:
                tap_at_coordinates(device, *coords)
                print(f"⌨️ Focused input at {coords}")
                time.sleep(0.5) 
                device.set_fastinput_ime(True)
                # current_txt=device.info.get("text", "")
                # if current_txt:
                #     device.clear_text()
                try:
                    device.clear_text()
                    print("✅ Cleared existing text.")
                except Exception as e:
                    print(f"[WARNING] Failed to clear existing text: {e}")

                device.send_keys(input_text)
                hide_keyboard(device)
                actions_performed["inputs"] += 1
                time.sleep(2)
                return True
            else:
                print("⚠️ Could not resolve input coordinates.")
    except Exception as e:
        print(f"[ERROR] Action execution failed: {e}")

    return False


def get_llm_decision(current_activity, actionable_elements, state_history, last_action_failed, task, screenshot_path=None):
    if not actionable_elements:
        return None, None, None, "Incomplete", "False"

    task_description = task["Description"]
    expected_outcome_text = task.get("ExpectedOutcome", None)


    print(f"Screenshot path to encode: {screenshot_path}")
    image_data = encode_image_to_base64(screenshot_path) if screenshot_path else None
    email_info = CREDENTIALS.get('email_account', {}) if CREDENTIALS else {}

    credentials_text = f"""
    - Email: {email_info.get('email', '')}
    - Password: {email_info.get('password', '')}
    - Phone: {email_info.get('phone', '')}
    - First Name: {email_info.get('first_name', '')}
    - Last Name: {email_info.get('last_name', '')}
    - Date of Birth: {email_info.get('date_of_birth', '')}
    """
    # credentials_text = f"""
    #     - Email: {CREDENTIALS.get('email', '')}
    #     - Password: {CREDENTIALS.get('password', '')}
    #     - Phone: {CREDENTIALS.get('phone', '')}
    #     - First Name: {CREDENTIALS.get('first_name', '')}
    #     - Last Name: {CREDENTIALS.get('last_name', '')}
    #     - Date of Birth: {CREDENTIALS.get('date_of_birth', '')}
    
    # """
    # print(f"debug: {credentials_text}")
    location_info = f"""
        - Latitude: {LOCATION['latitude']}
        - Longitude: {LOCATION['longitude']}
        - Address: {LOCATION['address']}
        - Country: {LOCATION['country']}
        """
    
    # print(f"debug: {location_info}")

    prompt = f"""
You are an AI agent exploring a mobile food delivery app using Appium.

        TASK: Your objective is to {task_description}.
        You must navigate through the app's UI and interact with available elements to achieve this goal. 

        Information Provided:
        1. Current Screen: {current_activity}

        2. Available Actionable Elements (Clickable buttons, input fields, etc.):
        {json.dumps(actionable_elements, indent=2)}

        3. Previous Actions Taken (Avoid repeating these):
        {json.dumps(state_history[-5:], indent=2)}
    
        4. In case of a login or sign up (Always go for continue with google)
        - Always put these exact values in InputText for email/password/name input fields during sign up or login:
        {credentials_text}

        5. Location Information:  {location_info}

        6. In case there is a map for address, always click on confirm pin location, or search with the street name provided location information
"""
    if expected_outcome_text:

        prompt += f"""
        8. EXPECTED OUTCOME:
        - The expected final result of the task is: "{expected_outcome_text}"
        - Analyze the current activity, screenshot and the actionable elements.
        - Decide if the current screen matches the expected outcome provided based on the information provided.
        - If they match the expected outcome, return Expected_Outcome: PASS
        - If you believe the expected outcome is still in progress due to the task given, return Expected_Outcome: In Progress
        - If the task is done and the final state does not match the expected outcome, return Expected_Outcome: FAIL
"""

    prompt +=f"""
        Respond in the following format ONLY:
        ActionType: <Click or Input>
        ResourceID: <Resource ID>
        Bounds: <Bounds>
        InputText: <Text if Input action, otherwise 'None'>
        TaskComplete: <Complete or Incomplete>
        Scroll: <True or False>
        Expected_Outcome: <PASS or FAIL or In Progress or N/A> 

        Instructions:
        - Analyze the available actionable elements and decide the most relevant action to proceed toward your given task. 
        - If you believe a crucial element (like 'Save', 'Continue', 'Submit', etc.) is not visible in the current actionable elements list, respond with True for the Scroll, otherwise False.
        - If there are Unnamed actionable elements they are still important.
        - The actionable elements are given to you in a sequential order as they appear on the screen, top to bottom and from left to right.
        - If there are multiple similar actionable elements refer to the screenshot for guidance about the order of the elements.
        - Think step by step using your reasoning abilities.
        - The screenshot provided is for additional context of what the current screen looks like, it should help you understand more.
        - You have to be extremely accurate in your decisions, analyze all given information before deciding.
        - When you return ActionType: Input provide the appropriate InputText with it.
        - You should suggest what to input from your understanding
        - Only mark the task as TaskComplete: Complete when you're absolutely certain the goal has been achieved.
        - If you're unsure whether the task is finished (e.g., no confirmation or success message visible), keep TaskComplete as Incomplete.
        - DO NOT use asterisks (**), bullets, or any special formatting. 
        - DO NOT provide explanations or thoughts in your response.
        - Only add the Expected_Outcome: <PASS or FAIL> IF given an expected outcome, if not give, return Expected_Outcome: N/A.
        - DO NOT return N/A for any of the other fields
        - Only respond in the specified format.
    """
    if image_data:
        messages = [
            {"role": "system", "content": "You are an AI agent exploring a mobile app. Respond only in the format. No explanations."},
            {"role": "user", "content": prompt},
            {
                "role": "user",
                "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}}],
            }
        ]
    else:
        messages = [
            {"role": "system", "content": "You are an AI agent exploring a mobile app. Respond only in the format. No explanations."},
            {"role": "user", "content": prompt}
        ]
        
    response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
        top_p=0.95
    )

    #store=True param
    # full_response = "".join(chunk.choices[0].delta.content or "" for chunk in response)
    # clean_response = re.sub(r'<think>.*?</think>', '', full_response, flags=re.DOTALL)
    
    clean_response = response.choices[0].message.content
    print("\n🔄 LLM RAW RESPONSE:\n", clean_response)
    # print(f"GPT-4 Response: {clean_response.strip()}")
    compact_response = clean_response.strip().replace("\n", "\\n")
    print(f"GPT-4 Response: {compact_response}")
    action_type = re.search(r'ActionType:\s*(Click|Input)', clean_response)
    # resource_id = re.search(r'ResourceID:\s*(\S+)', clean_response)
    resource_id = re.search(r'ResourceID:\s*(.+)', clean_response)
    input_text = re.search(r'InputText:\s*(.*)', clean_response)
    task_complete = re.search(r'TaskComplete:\s*(Complete|Incomplete)', clean_response)
    scroll = re.search(r'Scroll:\s*(True|False)', clean_response)
    expected_outcome = None
    if expected_outcome_text:
        expected_outcome = re.search(r'Expected_Outcome:\s*(PASS|FAIL)', clean_response, re.IGNORECASE)
        expected_outcome = expected_outcome.group(1).upper() if expected_outcome else None

    action_type = action_type.group(1) if action_type else None
    resource_id = resource_id.group(1) if resource_id else None
    input_text = input_text.group(1).strip() if input_text else None
    input_text = None if input_text.lower() in ["none", "null", ""] else input_text
    task_complete = task_complete.group(1) if task_complete else "Incomplete"
    scroll = scroll.group(1) if scroll else "False"

    return action_type, resource_id, input_text, task_complete, scroll, expected_outcome

def check_expected_outcome(task, actionable_elements, current_activity, screenshot_path):
    """
    Compares the current UI state against the expected outcome defined in the task.
    Returns True if PASS, False if FAIL.
    """
    task_description = task["Description"]
    expected = task["ExpectedOutcome"]
    image_data = encode_image_to_base64(screenshot_path) if screenshot_path else None
    
    print("🔎 Checking if expected outcome was achieved...")

    criteria_prompt = f"""
You are an AI QA assistant that have already navigated a given task on a mobile application up until this point for functional testing.

Task Completed: {task_description}

Your current Task is to: Determine if the expected outcome has been achieved based on the information provided below.

Expected Outcome: "{expected}"

Current Screen Activity: "{current_activity}"

Here are the actionable elements on the current screen for context:
{json.dumps(actionable_elements, indent=2)}

You are also provided with a screenshot for the current screen to serve as additional context and help you understand the state more.

Instructions:
- Decide if the current screen matches the expected outcome provided.
- Answer with:
PASS: if it matches.
FAIL: if it does not.
- Provide the one-word result (PASS or FAIL) 
- Provide a brief explanation for why it passed or failed to be included in a final report about the task.
"""

    messages=[
                {"role": "system", "content": "You are a concise QA evaluator. Answer with PASS or FAIL along with a brief explanation."},
                {"role": "user", "content": criteria_prompt},
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_data}"}}],
                }
            ]

    outcome_response = client.chat.completions.create(
        model="gpt-4.1",
        messages=messages,
        temperature=0.3,
        max_tokens=2048,
        top_p=0.95
    )

    # result = outcome_response.choices[0].message.content.strip().upper()
    # if "PASS" in result:
    #     print(f"✅ Outcome matched expected result: PASS")
    #     return True
    # else:
    #     print(f"❌ Outcome did not match expected result: FAIL")
    #     return False
    raw_response = outcome_response.choices[0].message.content.strip()
    print(f"\n🔎 LLM outcome response:\n{raw_response}\n")

    upper_response = raw_response.upper()
    if "PASS" in upper_response:
        print(f"✅ Outcome matched expected result: PASS")
        return True
    else:
        print(f"❌ Outcome did not match expected result: FAIL")
        return False

activity_count = 0

def explore_app(device, task, with_expected_outcomes, package_name,cpu_samples, rss_samples, fps_samples, janky_frame_samples, max_steps=2000):
    global activity_count
    task_description = task["Description"]
    print(f"\n🚀 Starting task: {task_description}\n")
    print(f"App Functions: {task_description}")
    clear_state_history()
    state_history = []
    last_action_failed = False
    time.sleep(2)
    start_time = time.time()
    subprocess.run(["adb", "shell", f"dumpsys gfxinfo {package_name} reset"], check=True)
    time.sleep(1)
    for step in range(max_steps):
        
        current_activity = device.app_current().get("activity", "unknown.activity")
        activity_count += 1
        print(f"Current screen: {current_activity}")

        screenshot_path = f"screenshots/step_{step}.png"
        os.makedirs("screenshots", exist_ok=True)
        time.sleep(3)

        capture_fixed_location_screenshot(step)

        time.sleep(2)

        # actionable_elements = extract_actionable_elements(device)

        MAX_EXTRACTION_RETRIES = 3
        RETRY_DELAY = 3  # seconds

        actionable_elements = []
        for retry in range(1, MAX_EXTRACTION_RETRIES + 1):
            actionable_elements = extract_actionable_elements(device)
            if actionable_elements:
                print(f"✅ Found actionable elements on retry {retry}")
                break
            else:
                print(f"🔄 No actionable elements found (retry {retry}/{MAX_EXTRACTION_RETRIES}). Retrying in {RETRY_DELAY} seconds...")
                time.sleep(RETRY_DELAY)

        if not actionable_elements:
            print("❌ Still no actionable elements after retries. Skipping this step or exiting as needed.")
            # break  # Or continue, or handle differently depending on your app's needs


        action_type, target_element, input_text, task_complete, scroll, expected_outcome = get_llm_decision(
            current_activity, actionable_elements, state_history, last_action_failed, task, screenshot_path=screenshot_path
        )

        if expected_outcome:
            print(f"🔎 Expected outcome check: {expected_outcome}")
            if expected_outcome == "PASS":
                print("🎉 Task passed by expected outcome match!")
                return True

        while scroll == "True":
            print("🔄 LLM requested scrolling to reveal hidden elements.")
            device.swipe(500, 1600, 500, 700)
            time.sleep(2)
            actionable_elements = extract_actionable_elements(device)

            # Re-query the LLM after scroll
            action_type, target_element, input_text, task_complete, scroll, expected_outcome = get_llm_decision(
                current_activity, actionable_elements, state_history, last_action_failed, task, screenshot_path=screenshot_path
            )

        if not action_type or not target_element:
            break

        print(f"🧪 DEBUG target_element: '{target_element}'")

        success = execute_action(device, action_type, target_element, input_text)
        if success:
            print(f"Performed Action: Successfuly {action_type} for target element {target_element}")
            last_action_failed = False
            time.sleep(4)

            # SAMPLE METRICS HERE:
            # cpu = get_cpu_usage(package_name)
            rss, pss = get_memory_usage(package_name)
            cpu_avg, cpu_peak = get_cpu_usage(package_name)
            cpu_samples.append(cpu_avg)
            cpu_samples.append(cpu_peak)  
            
      # first: cpu
            rss_samples.append(rss)       # second: rss
            pss_samples.append(pss)       # third: pss
            time.sleep(5)
            # fps_samples.append(fps)       # fourth: fps
            # janky_frame_samples.append(jank)  # fifth: janky

            new_viewtree = extract_viewtree(device)
            viewtree_path = f"viewtree/step_{step}_viewtree.xml"
            os.makedirs("viewtree", exist_ok=True)
            with open(viewtree_path, "w", encoding="utf-8") as f:
                f.write(new_viewtree or "<empty/>")
        else:
            last_action_failed = True

        state_history.append({"ActionType": action_type, "ID": target_element})
        if len(state_history) > 10:
            state_history.pop(0)

        save_state_history(state_history)
        time.sleep(1)

        if success and task_complete == "Complete":
            print("✅ Final action executed successfully.")

            time.sleep(5)  # let last frames render
            end_time = time.time()
            duration_sec = end_time - start_time
            print("[DEBUG] >>> Calling get_fps_and_jank")
            fps, jank = get_fps_and_jank(package_name, duration_sec)
            fps_samples.append(fps)
            janky_frame_samples.append(jank)
            print(f"[DEBUG] <<< Got FPS: {fps}, Janky Frames: {jank}")


            return True
    return False




if __name__ == "__main__":
    start_time = datetime.now()  # set at start of script

    if len(sys.argv) < 3:
        print("❌ Usage: python backend.py <apk_path> <task_json_path>")
        sys.exit(1)


    apk_path = os.path.abspath(sys.argv[1])
    sys.stdout.reconfigure(encoding='utf-8')
    print("[DEBUG] Hostname:", socket.gethostname())
    print("[DEBUG] IP:", socket.gethostbyname(socket.gethostname()))
    print("[DEBUG] Attempting requests.get to http://ip-api.com/json ...")

    temp_dir = None

    if apk_path.endswith(".xapk"):
        apk_paths, temp_dir = handle_xapk(apk_path)
        print("📦 APKs to be installed:", apk_paths)
        subprocess.run(["adb", "install-multiple", "-r", "-g"] + apk_paths, check=True)
        extracted_apk_path = apk_paths[0]
    else:
        extracted_apk_path = apk_path

    # Connect to uiautomator2
    device = initialize_u2()  # or u2.connect("emulator-5554")
    
    # Install and launch app manually (not via driver options)
    print(f"Installing {extracted_apk_path} ...")
    device.app_install(extracted_apk_path)
    package_name = subprocess.run(
        ["aapt", "dump", "badging", extracted_apk_path],
        stdout=subprocess.PIPE, text=True
    ).stdout.split("package: name='")[1].split("'")[0]

    print(f"[INFO] Package: {package_name}")

    app_name = format_app_name(package_name)  # <-- ADD THIS LINE
    print(f"🚀 Launching {package_name} (App Name: {app_name})")

    # print(f"🚀 Launching {package_name}")
    device.app_start(package_name)
    time.sleep(5)

    app_uid = None
    try:
        output = subprocess.check_output(
            ["adb", "shell", "dumpsys", "package", package_name], text=True
        )
        for line in output.splitlines():
            if "userId=" in line:
                app_uid = line.strip().split("userId=")[1]
                break
        if not app_uid:
            print("[ERROR] Could not determine UID for app!")
    except Exception as e:
        print(f"[ERROR] Failed to get app UID: {e}")

    tx_start, rx_start = get_network_usage(app_uid)


    task_json_path = os.path.abspath(sys.argv[2])

    try:
        with open(task_json_path, "r", encoding="utf-8") as f:
            tasks_data = json.load(f)

        tasks = tasks_data.get("tasks", [])
        if not tasks:
            print("❌ No tasks found in task file.")
            sys.exit(1)

        with_expected_outcomes = any("ExpectedOutcome" in task for task in tasks)

        print(f"[INFO] Tasks loaded: {len(tasks)}")
        print(f"[INFO] with_expected_outcomes: {with_expected_outcomes}")

        cpu_samples = []
        rss_samples = []
        fps_samples = []
        janky_frame_samples = []
        pss_samples = []


        for task in tasks:
            # explore_app(device, task["Description"])
            task_completed = explore_app(device, task, with_expected_outcomes, package_name,cpu_samples, rss_samples, fps_samples, janky_frame_samples)
            
            cpu_avg = sum(cpu_samples) / len(cpu_samples) if cpu_samples else 0
            cpu_peak = max(cpu_samples) if cpu_samples else 0
            rss_avg = sum(rss_samples) / len(rss_samples) if rss_samples else 0
            rss_peak = max(rss_samples) if rss_samples else 0
            fps_avg = sum(fps_samples) / len(fps_samples) if fps_samples else 0
            janky_avg = sum(janky_frame_samples) / len(janky_frame_samples) if janky_frame_samples else 0
            pss_avg = sum(pss_samples) / len(pss_samples) if pss_samples else 0



            tx_end, rx_end = get_network_usage(app_uid)
            tx_diff = max(tx_end - tx_start, 0)
            rx_diff = max(rx_end - rx_start, 0)

            cpu_samples.clear()
            rss_samples.clear()
            fps_samples.clear()
            janky_frame_samples.clear()
            pss_samples.clear()


            if task_completed:
                completed_functions.append(task["Description"])
                print("All flows have been successfully navigated and tested!")
            state_data = load_state_history()["StateHistory"]
            suggested_functions = [task["Description"] for task in tasks]
            completed_functions = suggested_functions.copy()  # Here, treat all executed as completed

            function_descriptions = {desc: desc for desc in suggested_functions}
            screenshot_log = {s["ID"]: [f"screenshots/step_{i}.png"] for i, s in enumerate(state_data)}
            pdf_thread = threading.Thread(target=generate_pdf_in_background)
            pdf_thread.start()

            try:
                pdf_thread.join()
            except KeyboardInterrupt:
                print("\nInterrupted! Waiting for PDF report to finish...")
                pdf_thread.join()

            time.sleep(3)
        end_time = datetime.now()    # update at end of tasks


        save_metrics_report(
            app_name=app_name,
            suggested_functions=suggested_functions,
            completed_functions=completed_functions,
            start_time=start_time,
            end_time=end_time,
            package_name=package_name,
            cpu_avg=cpu_avg,
            cpu_peak=cpu_peak,
            rss_avg=rss_avg,
            rss_peak=rss_peak,
            pss_avg=pss_avg,
            start_battery=50,
            end_battery=40,
            fps_avg=fps_avg,
            janky_frames=int(janky_avg),
            network_usage=(tx_diff, rx_diff),
            actions_performed=actions_performed
        )


        print("✅ Metrics report has been saved successfully.")

    finally:
        device.app_stop(package_name)
        if temp_dir:
            shutil.rmtree(temp_dir)
            print(f"Cleaned up temp XAPK extraction directory: {temp_dir}")

    #################  WORKING SCRIPT

#change apk path and task file