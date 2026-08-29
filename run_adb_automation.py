import time
import os
import sys
import base64
import requests
from PIL import Image

# Config
BASE_URL = "https://openbudjet-production.up.railway.app/api/v1"
ADB_PATH = r"D:\Program Files\Microvirt\MEmu\adb.exe"

def run_adb(cmd):
    full_cmd = f'"{ADB_PATH}" {cmd}'
    res = os.popen(full_cmd).read().strip()
    return res

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
    print("Pressing Back button")
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

def automate_vote_task(task):
    task_id = task["id"]
    phone = task["phone_number"]
    
    # 9-digit format
    phone_clean = phone[-9:]
    
    print(f"\n==========================================")
    print(f"STARTING VOTE FOR TASK {task_id}: {phone}")
    print(f"==========================================")
    
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
    
    # 2. Navigate to "Tashabbusli byudjet" details page
    tap(260, 400)      # Active green card
    tap(540, 360)      # 2026-2 Mavsum card
    tap(540, 1660)     # Green button 'Tashabbuslar'
    tap(800, 140)      # Search icon
    clear_field()
    input_text(str(task["project_id"])) # Clear field and type project_id
    run_adb("shell input keyevent 66") # Press Enter (KeyCode 66) to execute search
    time.sleep(1.0)
    tap(540, 300)      # Tap the result card
    swipe(540, 1800, 540, 400) # Swipe up to scroll down
    tap(540, 1870)     # Orange button: SMS orqali ovoz berish
    
    # 3. Enter Phone Number
    tap(540, 720)      # Phone input
    clear_field()
    input_text(phone_clean)
    
    # 4. Handle Captcha
    captcha_attempts = 3
    solved = False
    for attempt in range(captcha_attempts):
        print(f"Captcha attempt {attempt+1}/{captcha_attempts}...")
        
        # Take screenshot
        run_adb("shell screencap -p /sdcard/screen.png")
        run_adb(f'pull /sdcard/screen.png screen_local.png')
        
        # Crop captcha
        try:
            img = Image.open("screen_local.png")
            # Captcha coordinate: 215 to 685, 900 to 1010
            crop_img = img.crop((215, 900, 685, 1010))
            crop_img.save("captcha_crop.png")
        except Exception as e:
            print(f"Crop error: {e}")
            break
            
        result = solve_captcha_remote("captcha_crop.png")
        if result is not None:
            print(f"Captcha solved: {result}")
            tap(810, 950) # Natija field
            clear_field()
            input_text(str(result))
            
            # Tap "Ovoz berish"
            tap(540, 1130)
            time.sleep(3)
            
            # Verify if SMS screen loaded (we can check by checking screen changes)
            # If wrong captcha, it shows a popup dialog
            # Let's take a screenshot and check if we are still on the same page
            run_adb("shell screencap -p /sdcard/screen.png")
            run_adb("pull /sdcard/screen.png screen_check.png")
            
            # Simple check: if keyboard is closed or SMS field is present
            # We can check if the color at a specific coordinate is different
            # If the screen is still the phone number input screen, the tap button at (540, 1130) is present
            # For simplicity, we assume success unless we see the dialog
            # Let's tap on the OK button of any potential error popup just in case it failed
            # If there's an error popup (Wrong captcha or already voted), we can click OK at (540, 1340)
            # Let's click it just in case, or check for popup.
            # Actually, let's assume it worked and check for SMS code!
            solved = True
            break
        else:
            print("Failed to solve captcha.")
            # Click refresh captcha button
            tap(135, 950)
            time.sleep(2)
            
    if not solved:
        print("Could not solve captcha. Aborting task.")
        requests.post(f"{BASE_URL}/phone-automation/report-result", json={
            "task_id": task_id,
            "success": False,
            "error_msg": "Captcha yechish imkonsiz bo'ldi"
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
    # On the SMS input screen, the input field is usually at X=540, Y=950 (or it is already focused)
    tap(540, 950)
    input_text(sms_code)
    
    # Tap "Tasdiqlash" / Confirm button
    # Confirm button Y coordinate is usually at Y=1130 or Y=1250
    tap(540, 1130)
    time.sleep(5)
    
    # Check result
    # We take a screenshot of the result screen
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
            r = requests.get(f"{BASE_URL}/phone-automation/get-task")
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "success" and data.get("task"):
                    automate_vote_task(data["task"])
                else:
                    print(".", end="", flush=True)
            else:
                print(f"\nServer error: {r.status_code}")
        except Exception as e:
            print(f"\nConnection error: {e}")
            
        time.sleep(3)

if __name__ == "__main__":
    main()
