import time
import os
import sys
import base64
import subprocess
import requests
from PIL import Image

# Config
BASE_URL = "https://openbudjet-production.up.railway.app/api/v1"
ADB_PATH = r"D:\Program Files\Microvirt\MEmu\adb.exe"

# Global state for pre-solved captcha
CURRENT_PRE_SOLVED_CAPTCHA = None
CURRENT_PRE_SOLVED_PROJECT_ID = None

def run_adb(cmd):
    full_cmd = f'"{ADB_PATH}" {cmd}'
    try:
        res = subprocess.run(full_cmd, shell=True, capture_output=True, text=True, timeout=15)
        return res.stdout.strip()
    except subprocess.TimeoutExpired:
        print(f"ADB command timed out: {full_cmd}")
        return ""
    except Exception as e:
        print(f"ADB command error: {cmd} - {e}")
        return ""

def tap(x, y):
    print(f"Tapping: ({x}, {y})")
    run_adb(f"shell input tap {x} {y}")
    time.sleep(0.5)

def swipe(x1, y1, x2, y2, duration=300):
    print(f"Swiping: ({x1}, {y1}) -> ({x2}, {y2})")
    run_adb(f"shell input swipe {x1} {y1} {x2} {y2} {duration}")
    time.sleep(0.6)

def input_text(text):
    print(f"Typing text: {text}")
    run_adb(f'shell input text "{text}"')
    time.sleep(0.2)

def clear_field():
    print("Clearing field...")
    # Send KEYCODE_MOVE_END
    run_adb("shell input keyevent 123") 
    # Send KEYCODE_DEL 15 times
    for _ in range(15):
        run_adb("shell input keyevent 67")
    time.sleep(0.2)

def press_back():
    print("Pressing BACK...")
    run_adb("shell input keyevent 4")
    time.sleep(0.5)

def solve_captcha_remote(crop_path):
    with open(crop_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode('utf-8')
    
    url = f"{BASE_URL}/phone-automation/solve-captcha"
    try:
        r = requests.post(url, json={"image_base64": img_b64}, timeout=30)
        if r.status_code == 200:
            return r.json().get("result")
    except Exception as e:
        print(f"Captcha solve error: {e}")
    return None

def get_xml_dump():
    try:
        # Delete old dump from device to avoid cache issues
        run_adb("shell rm -f /sdcard/window_dump.xml")
        run_adb("shell uiautomator dump /sdcard/window_dump.xml")
        temp_path = "window_dump_temp.xml"
        
        # Pull the file if it exists
        res = run_adb(f"pull /sdcard/window_dump.xml {temp_path}")
        if "error" in res.lower() or "not found" in res.lower() or not os.path.exists(temp_path):
            return ""
            
        with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
            xml_content = f.read()
        try:
            os.remove(temp_path)
        except Exception:
            pass
        return xml_content
    except Exception as e:
        print(f"Error getting XML dump: {e}")
    return ""

def reset_to_dashboard():
    print("Checking/Resetting to dashboard if needed...")
    for i in range(4):
        xml = get_xml_dump()
        if "Tashabbuslar doskasi" in xml:
            print("Dashboard ('Tashabbuslar doskasi') detected.")
            return True
        print(f"'Tashabbuslar doskasi' not found. Pressing BACK (attempt {i+1}/4)...")
        run_adb("shell input keyevent 4")
        time.sleep(1.0)
    
    xml = get_xml_dump()
    if "Tashabbuslar doskasi" in xml:
        print("Dashboard ('Tashabbuslar doskasi') detected on final check.")
        return True
    print("Could not reset to dashboard.")
    return False

def check_captcha_submission_result():
    xml = get_xml_dump()
    if not xml:
        return 'UNKNOWN'
        
    if "uz.minfin.open_budget" not in xml:
        return 'UNKNOWN'
        
    if "Matematik amalni hisoblang" not in xml:
        if "SMS orqali ovoz berish" in xml or "Sms" in xml or "Tasdiqlash" in xml:
            return 'SUCCESS'
        else:
            return 'UNKNOWN'
            
    lower_xml = xml.lower()
    
    if "noto'g'ri" in lower_xml or "xato" in lower_xml or "kod" in lower_xml or "invalid" in lower_xml:
        print("Wrong captcha detected.")
        tap(540, 1340)
        time.sleep(0.5)
        tap(540, 1200)
        time.sleep(0.5)
        return 'WRONG_CAPTCHA'
        
    if "allaqachon" in lower_xml or "ovoz berilgan" in lower_xml or "already_voted" in lower_xml:
        print("Phone already voted detected.")
        tap(540, 1340)
        time.sleep(0.5)
        tap(540, 1200)
        time.sleep(0.5)
        return 'ALREADY_VOTED'
        
    return 'STILL_ON_INPUT'

def prepare_pre_solved_captcha():
    global CURRENT_PRE_SOLVED_CAPTCHA, CURRENT_PRE_SOLVED_PROJECT_ID
    
    url = f"{BASE_URL}/phone-automation/active-project"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                active_project_id = str(data.get("project_id"))
            else:
                print("No active project returned by server.")
                return False
        else:
            print(f"Failed to get active project: server returned status {r.status_code}")
            return False
    except Exception as e:
        print(f"Error getting active project: {e}")
        return False

    if CURRENT_PRE_SOLVED_CAPTCHA is not None and CURRENT_PRE_SOLVED_PROJECT_ID == active_project_id:
        return True

    print(f"\nPre-solving captcha for active project: {active_project_id}...")
    
    # 1. Wake up and unlock
    run_adb("shell input keyevent KEYCODE_WAKEUP")
    time.sleep(0.3)
    run_adb("shell input swipe 540 2000 540 500 200")
    time.sleep(0.5)
    
    # 2. Reset state (go home, open app)
    run_adb("shell input keyevent 3")
    time.sleep(0.5)
    
    # Launch OpenBudget
    print("Launching OpenBudget for pre-solving...")
    run_adb("shell monkey -p uz.minfin.open_budget -c android.intent.category.LAUNCHER 1")
    time.sleep(2.5)
    reset_to_dashboard()
    
    # 3. Navigate to "Tashabbusli byudjet" details page
    tap(260, 400)      # Active green card
    tap(540, 360)      # 2026-2 Mavsum card
    tap(540, 1660)     # Green button 'Tashabbuslar'
    tap(800, 140)      # Search icon
    clear_field()
    input_text(active_project_id)
    run_adb("shell input keyevent 66") # Press Enter
    time.sleep(1.0)
    
    # Tap the result card
    tap(540, 300)
    swipe(540, 1800, 540, 400) # Swipe up to scroll down
    tap(540, 1870)     # Orange button: SMS orqali ovoz berish
    time.sleep(1.0)
    
    # 4. Take screenshot and crop captcha
    run_adb("shell screencap -p /sdcard/screen.png")
    run_adb("pull /sdcard/screen.png screen_local.png")
    
    try:
        img = Image.open("screen_local.png")
        crop_img = img.crop((215, 900, 685, 1010))
        crop_img.save("captcha_crop.png")
    except Exception as e:
        print(f"Crop error: {e}")
        return False
        
    result = solve_captcha_remote("captcha_crop.png")
    if result is not None:
        print(f"Pre-solved captcha successfully: {result}")
        CURRENT_PRE_SOLVED_CAPTCHA = result
        CURRENT_PRE_SOLVED_PROJECT_ID = active_project_id
        return True
    else:
        print("Failed to pre-solve captcha.")
        CURRENT_PRE_SOLVED_CAPTCHA = None
        CURRENT_PRE_SOLVED_PROJECT_ID = None
        return False

def automate_vote_task(task):
    task_id = task["id"]
    phone = task["phone_number"]
    
    global CURRENT_PRE_SOLVED_CAPTCHA, CURRENT_PRE_SOLVED_PROJECT_ID
    
    task_id = task["id"]
    phone = task["phone_number"]
    
    # 9-digit format
    phone_clean = phone[-9:]
    project_id = str(task["project_id"])
    
    print(f"\n==========================================")
    print(f"STARTING VOTE FOR TASK {task_id}: {phone}")
    print(f"==========================================")
    
    # Determine if we can use the pre-solved captcha
    use_pre_solved = (
        CURRENT_PRE_SOLVED_CAPTCHA is not None 
        and CURRENT_PRE_SOLVED_PROJECT_ID == project_id
    )
    
    success_nav = False
    status = 'UNKNOWN'
    
    if use_pre_solved:
        print(f"Pre-solved captcha is available for project {project_id}: {CURRENT_PRE_SOLVED_CAPTCHA}")
        # Try to use the pre-solved captcha directly
        # Wake up screen and unlock
        print("Waking up screen and unlocking...")
        run_adb("shell input keyevent KEYCODE_WAKEUP")
        time.sleep(0.3)
        run_adb("shell input swipe 540 2000 540 500 200") # Unlock swipe lock
        time.sleep(0.5)
        
        # Enter phone
        tap(540, 720)      # Phone input
        clear_field()
        input_text(phone_clean)
        
        # Enter pre-solved captcha
        print(f"Entering pre-solved captcha: {CURRENT_PRE_SOLVED_CAPTCHA}")
        tap(810, 950) # Natija field
        clear_field()
        input_text(str(CURRENT_PRE_SOLVED_CAPTCHA))
        
        # Tap "Ovoz berish"
        tap(540, 1130)
        time.sleep(3)
        
        # Check result
        status = check_captcha_submission_result()
        print(f"Pre-solved submission status: {status}")
        
        if status == 'SUCCESS':
            success_nav = True
            print("Pre-solved captcha accepted successfully!")
        elif status == 'ALREADY_VOTED':
            print("This phone number has already voted (detected via pre-solved submission).")
            requests.post(f"{BASE_URL}/phone-automation/report-result", json={
                "task_id": task_id,
                "success": False,
                "error_msg": "Bu raqam orqali allaqachon ovoz berilgan"
            })
            return
        elif status == 'WRONG_CAPTCHA':
            print("Pre-solved captcha was wrong. Staying on the screen to solve on the fly.")
            # Since we are already on the correct project's screen, we don't need to re-navigate!
            success_nav = True
        else: # STILL_ON_INPUT or UNKNOWN
            print("Pre-solved flow failed/unexpected state. Resetting and falling back to standard flow...")
            # We do NOT set success_nav to True, so it will fall back to standard navigation below
            pass

    # If pre-solved was not used or failed to navigate/submit, run the standard flow
    if not success_nav:
        print("Running standard navigation flow...")
        # Wake up screen and unlock
        print("Waking up screen and unlocking...")
        run_adb("shell input keyevent KEYCODE_WAKEUP")
        time.sleep(0.3)
        run_adb("shell input swipe 540 2000 540 500 200") # Unlock swipe lock
        time.sleep(0.5)
        
        # 1. Reset state (go home, open app)
        run_adb("shell input keyevent 3") # Home
        time.sleep(0.5)
        
        # Launch OpenBudget
        print("Launching OpenBudget...")
        run_adb("shell monkey -p uz.minfin.open_budget -c android.intent.category.LAUNCHER 1")
        time.sleep(2.5)
        reset_to_dashboard()
        
        # 2. Navigate to "Tashabbusli byudjet" details page
        tap(260, 400)      # Active green card
        tap(540, 360)      # 2026-2 Mavsum card
        tap(540, 1660)     # Green button 'Tashabbuslar'
        tap(800, 140)      # Search icon
        clear_field()
        input_text(project_id) # Clear field and type project_id
        run_adb("shell input keyevent 66") # Press Enter (KeyCode 66) to execute search
        time.sleep(1.0)
        tap(540, 300)      # Tap the result card
        swipe(540, 1800, 540, 400) # Swipe up to scroll down
        tap(540, 1870)     # Orange button: SMS orqali ovoz berish
        time.sleep(1.0)
        
    # Now we are on the SMS orqali ovoz berish page (either via pre-solved screen or standard navigation).
    solved = (success_nav and status == 'SUCCESS')
    
    if not solved:
        captcha_attempts = 3
        for attempt in range(captcha_attempts):
            print(f"Captcha attempt {attempt+1}/{captcha_attempts}...")
            
            # Take screenshot
            run_adb("shell screencap -p /sdcard/screen.png")
            run_adb("pull /sdcard/screen.png screen_local.png")
            
            # Crop captcha
            try:
                img = Image.open("screen_local.png")
                # Captcha coordinate: 215 to 685, 900 to 1010
                crop_img = img.crop((215, 900, 685, 1010))
                crop_img.save("captcha_crop.png")
            except Exception as e:
                print(f"Crop error: {e}")
                tap(135, 950) # Refresh captcha
                time.sleep(2)
                continue
                
            result = solve_captcha_remote("captcha_crop.png")
            if result is None:
                print("Failed to solve captcha remotely.")
                tap(135, 950) # Refresh captcha
                time.sleep(2)
                continue
                
            print(f"Captcha solved: {result}")
            
            # Enter phone number (just in case)
            tap(540, 720)      # Phone input
            clear_field()
            input_text(phone_clean)
            
            # Enter captcha
            tap(810, 950) # Natija field
            clear_field()
            input_text(str(result))
            
            # Tap "Ovoz berish"
            tap(540, 1130)
            time.sleep(3)
            
            # Check result of submission
            status = check_captcha_submission_result()
            print(f"Submission status: {status}")
            
            if status == 'SUCCESS':
                solved = True
                break
            elif status == 'ALREADY_VOTED':
                print("This phone number has already voted.")
                requests.post(f"{BASE_URL}/phone-automation/report-result", json={
                    "task_id": task_id,
                    "success": False,
                    "error_msg": "Bu raqam orqali allaqachon ovoz berilgan"
                })
                return
            elif status == 'WRONG_CAPTCHA':
                print("Captcha was wrong. Retrying...")
                time.sleep(1.0)
                continue
            else: # STILL_ON_INPUT or UNKNOWN
                print("Still on input page or unknown state. Refreshing captcha and retrying...")
                tap(135, 950)
                time.sleep(2)
                continue
                
    if not solved:
        print("Could not solve captcha. Aborting task.")
        requests.post(f"{BASE_URL}/phone-automation/report-result", json={
            "task_id": task_id,
            "success": False,
            "error_msg": "Captchani yechish imkonsiz bo'ldi"
        })
        return

    # 5. Report SMS Sent to bot
    print("Reporting SMS sent...")
    requests.post(f"{BASE_URL}/phone-automation/report-sms-sent", json={"task_id": task_id})
    
    # 6. Wait for user to input SMS code in Telegram
    print("Waiting for SMS code from user...")
    sms_code = None
    timeout = 180 # 3 minutes
    start_time = time.time()
    while time.time() - start_time < timeout:
        time.sleep(3)
        try:
            r = requests.get(f"{BASE_URL}/phone-automation/get-sms-code", params={"task_id": task_id})
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "success":
                    sms_code = data.get("sms_code")
                    print(f"SMS code received: {sms_code}")
                    break
                elif data.get("status") == "aborted":
                    print("Task was cancelled or aborted by server.")
                    return
        except Exception as e:
            print(f"Error checking SMS code: {e}")
            
    if not sms_code:
        print("SMS code timeout. Aborting task.")
        requests.post(f"{BASE_URL}/phone-automation/report-result", json={
            "task_id": task_id,
            "success": False,
            "error_msg": "SMS tasdiqlash kodi kiritilmadi (Kutilgan vaqt tugadi)"
        })
        return
        
    # 7. Input SMS code
    tap(540, 950)
    input_text(sms_code)
    
    # Tap "Tasdiqlash" / Confirm button
    tap(540, 1130)
    time.sleep(5)
    
    # Check result
    run_adb("shell screencap -p /sdcard/screen.png")
    run_adb("pull /sdcard/screen.png screen_result.png")
    
    # Report success
    print("Reporting success to server!")
    requests.post(f"{BASE_URL}/phone-automation/report-result", json={
        "task_id": task_id,
        "success": True
    })
    print("Task completed successfully!")

def main():
    global CURRENT_PRE_SOLVED_CAPTCHA, CURRENT_PRE_SOLVED_PROJECT_ID
    
    print("==================================================")
    print("OpenBudget Phone ADB Automation System Active")
    print(f"Polling: {BASE_URL}")
    print("==================================================")
    
    # Verify device
    devices = run_adb("devices")
    if "device" not in devices.split("\n")[-2] and "device" not in devices.split("\n")[-3]:
        print("No ADB devices connected. Please connect your phone via USB and enable USB Debugging.")
        print(devices)
        return
        
    print("Connected device detected successfully.")
    
    while True:
        try:
            # When idle (no task), if we don't have a pre-solved captcha, pre-solve one
            if CURRENT_PRE_SOLVED_CAPTCHA is None:
                prepare_pre_solved_captcha()
                
            r = requests.get(f"{BASE_URL}/phone-automation/get-task")
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "success" and data.get("task"):
                    try:
                        automate_vote_task(data["task"])
                    finally:
                        # Clear pre-solved captcha so that it is solved again for next task
                        CURRENT_PRE_SOLVED_CAPTCHA = None
                        CURRENT_PRE_SOLVED_PROJECT_ID = None
                else:
                    print(".", end="", flush=True)
            else:
                print(f"\nServer error: {r.status_code}")
        except Exception as e:
            print(f"\nConnection error: {e}")
            
        time.sleep(3)

if __name__ == "__main__":
    main()
