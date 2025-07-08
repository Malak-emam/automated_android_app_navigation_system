import json
import subprocess
import re
import os
from dotenv import load_dotenv
from openai import OpenAI
import sys

sys.stdout.reconfigure(encoding='utf-8')

# ✅ Load API key from .env
load_dotenv()
client = OpenAI(api_key=os.getenv("API_KEY"))

# ✅ Configs
# apk_path = "C:\\new_drive\\GraduationProject\\APKs\\ToDoList.apk"
# apk_path = "C:\\new_drive\\GraduationProject\\APKs\\talabat.apk"
# apk_path = "C:\\new_drive\\GraduationProject\\APKs\\spotify.apk"
if len(sys.argv) < 2:
    print("❌ Usage: python metadata2.py <apk_path>")
    sys.exit(1)

apk_path = sys.argv[1]

if not os.path.exists(apk_path):
    print(f"❌ APK file does not exist: {apk_path}")
    sys.exit(1)

script_dir = os.path.dirname(os.path.abspath(__file__))
app_metadata_file = os.path.join(script_dir, "app_metadata.json")
llm_tasks_file = os.path.join(script_dir, "llm_tasks.json")

# ✅ Extract Package Name from APK
def get_package_name(apk_path):
    try:
        result = subprocess.run(["aapt", "dump", "badging", apk_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output = result.stdout
        for line in output.splitlines():
            if "package: name=" in line:
                return line.split("'")[1]
    except Exception as e:
        print(f"❌ Error extracting package name: {e}")
        return None

# ✅ Extract Activities from AndroidManifest.xml
def extract_activities_from_apk(apk_path, package_name):
    activities = []
    try:
        result = subprocess.run(
            ["aapt", "dump", "xmltree", apk_path, "AndroidManifest.xml"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        if result.returncode != 0:
            print(f"❌ Error running aapt: {result.stderr}")
            return activities

        lines = result.stdout.splitlines()
        current_tag = None

        for line in lines:
            line = line.strip()
            if line.startswith("E: activity"):
                current_tag = "activity"
            elif line.startswith("A: android:name") and current_tag == "activity":
                activity_name = line.split('"')[1]
                if activity_name.startswith("."):
                    activity_name = f"{package_name}{activity_name}"
                activities.append(activity_name)
                current_tag = None

    except Exception as e:
        print(f"❌ Error extracting activities: {e}")
    return activities

# ✅ GPT-4.1 Response Handler using new SDK
def get_llm_response(prompt):
    try:
        response = client.chat.completions.create(
            model="gpt-4.1",  # or "gpt-4", "gpt-3.5-turbo"
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.6,
            top_p=0.95,
            max_tokens=2048  # ✅ correct key in this API
        )

        result = response.choices[0].message.content
        print("✅ GPT-4.1 Response:")
        print(result)
        #clean_response = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL)
        return result
    except Exception as e:
        print(f"❌ Error from LLM: {e}")
        return None


# ✅ Extract clean JSON array from LLM output
def extract_json_list(text):
    match = re.search(r'\[\s*{.*?}\s*]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            print("⚠️ JSON decoding failed.")
            return []
    else:
        print("⚠️ Could not extract JSON list from LLM response.")
        print("LLM said:\n", text)
        return []

# ✅ Get app category from package name
def get_app_category(package_name):
    prompt = f"""
Given the Android package name: "{package_name}", classify the most likely category of the app.

Choose only from:
- Productivity
- Social Media
- E-Commerce
- Finance
- Travel
- Messaging
- Health & Fitness
- Utility
- Entertainment
- Food Delivery
- Other (briefly describe)

IMPORTANT: ONLY return this JSON format and nothing else:
{{ "Category": "<App Category>" }}
"""
    response = get_llm_response(prompt)
    if not response:
        return "Unknown"
    try:
        return json.loads(response).get("Category", "Unknown")
    except:
        match = re.search(r'{\s*"Category"\s*:\s*"(.+?)"\s*}', response)
        if match:
            return match.group(1).strip()
        print("⚠️ Failed to parse category. LLM said:", response)
        return "Unknown"

# ✅ Generate tasks based on category and activities
def suggest_tasks(package_name, category, activities):
    prompt = f"""
You are an Android app analyst.

Given this app info:
- Package Name: "{package_name}"
- App Category: "{category}"
- Activities:
{json.dumps(activities, indent=2)}

We need to test the happy paths of this application.
Suggest 5-8 clear, meaningful, logical and usual user tasks ordered by priority (most common/important first).

Return ONLY a JSON array like:
[
  {{ "TaskID": "Task1", "Description": "Create a new task" }},
  {{ "TaskID": "Task2", "Description": "View existing tasks" }}
]

DO NOT include any markdown, explanation, or comments. Only return the JSON array directly.
"""
    raw_response = get_llm_response(prompt)
    return extract_json_list(raw_response)

# ✅ === MAIN EXECUTION ===
if __name__ == "__main__":
    apk_package_name = get_package_name(apk_path)
    if not apk_package_name:
        print("❌ Failed to extract package name.")
        exit()

    activities = extract_activities_from_apk(apk_path, apk_package_name)
    if not activities:
        print("⚠️ No activities extracted.")
        exit()

    print(f"✅ Extracted {len(activities)} activities.")

    metadata = {
        "package_name": apk_package_name,
        "activities": activities
    }
    with open(app_metadata_file, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4)
    print(f"✅ App metadata saved to: {app_metadata_file}")

    category = get_app_category(apk_package_name)
    print(f"📦 Detected App Category: {category}")

    tasks = suggest_tasks(apk_package_name, category, activities)
    if not tasks:
        print("❌ Failed to extract tasks.")
        exit()

    llm_output = {
        "category": category,
        "tasks": tasks
    }
    with open(llm_tasks_file, "w", encoding="utf-8") as f:
        json.dump(llm_output, f, indent=4)
    print(f"✅ Suggested {len(tasks)} tasks saved to: {llm_tasks_file}")
