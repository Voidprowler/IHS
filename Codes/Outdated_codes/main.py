import sys
import os

# MUST BE FIRST - fix module path for PyInstaller before any other imports
if hasattr(sys, '_MEIPASS'):
    sys.path.insert(0, sys._MEIPASS)

import ctypes
import logging

# Self-elevate if not admin
def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

if not is_admin():
    ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, " ".join(sys.argv), None, 1
    )
    sys.exit()

# Redirect stdout when running as .exe to prevent crashes with console=False
if hasattr(sys, '_MEIPASS'):
    sys.stdout = open('sentinel_debug.log', 'a', encoding='utf-8')
    sys.stderr = sys.stdout


import time
from sensors import HardwareSensors
from alerts import AlertSystem
from config import *


# Does user want to display Dashboard
SHOW_DASHBOARD = '--dashboard' in sys.argv 

def main(sensors=None):
    if sensors is None:
        sensors = HardwareSensors()  # Only creates if not passed in
    alerts = AlertSystem()
    
    # Initial check to log what is found at startup
    initial_stats = sensors.get_system_stats()
    logging.info(f"RAW STARTUP STATS: {initial_stats}")
    alerts.log_system_profile(initial_stats)
    
    print("\n" + "="*50)
    print("      INTELLIGENT HARDWARE SENTINEL ACTIVE      ")
    print("="*50 + "\n")


    loop_count = 0
    try:
        while True:
            loop_count += 1
            stats = sensors.get_system_stats()
            
            # Log every 10 iterations (every 20 seconds) to confirm loop is running
            if loop_count % 10 == 0:
                logging.info(f"Loop iteration {loop_count}: {stats}")
            
        
            # 1. Gather all data
            #stats = sensors.get_system_stats()
            
            # 2. Build the Display String
            # We use .get() or checks to handle None values gracefully
            cpu = f"CPU: {stats['cpu_temp']}°C" if stats['cpu_temp'] else "CPU: Locked"
            gpu = f"GPU: {stats['gpu_temp']}°C" if stats['gpu_temp'] else "GPU: N/A"
            ssd_t = f"SSD: {stats['ssd_temp']}°C" if stats['ssd_temp'] else "SSD: N/A"
            ssd_h = f"Health: {stats['ssd_health']}%" if stats['ssd_health'] else "Health: N/A"
            ram = f"RAM: {stats['ram_percent']}%"

            # Print status line (using \r to overwrite the same line)
            status_line = f"| {cpu} | {gpu} | {ssd_t} | {ssd_h} | {ram} |"
            if not hasattr(sys, '_MEIPASS'):
                print(status_line, end="\r")

            # 3. Alert Logic
            # CPU Warning (between 80°C and 89°C ONLY)
            if stats['cpu_temp'] and CPU_TEMP_WARN < stats['cpu_temp'] < CPU_TEMP_CRITICAL:
                alerts.notify("⚠️ CPU HOT",
                              f"Temperature hit {stats['cpu_temp']}°C\n"
                              "\u2192 Close heavy programs\n"
                              "\u2192 Check ventilation")
                
            # CPU Critical (90°C and above)
            if stats['cpu_temp'] and stats['cpu_temp'] >= CPU_TEMP_CRITICAL:
                alerts.notify("🔥 CPU CRITICAL", 
                              f"Temperature hit {stats['cpu_temp']}°C\n"
                              "\u2192 Check if fans are running\n"
                              "\u2192 Elevate Laptop\n"
                              "\u2192 Close resource-heavy programs")

            # RAM Alerts
            if stats['ram_percent'] > RAM_PERCENT_LIMIT:
                alerts.notify("⚠️ RAM WARNING", 
                              f"Memory usage is high: {stats['ram_percent']}%\n"
                              "\u2192 Open Task Manager (Ctrl+Shift+Esc keys)\n"
                              "\u2192 Close unnecessary programs")

            # SSD Health Alerts
            if stats['ssd_health'] and stats['ssd_health'] < 20:
                alerts.notify("🛑 SSD CRITICAL", 
                              f"SSD Life Remaining: {stats['ssd_health']}%\n"
                              "\u2192 BACKUP YOUR DATA NOW\n"
                              "\u2192 Plan drive replacement")

            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nSentinel shutting down safely...")
    except Exception as e:
        logging.error(f"FATAL ERROR: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    if SHOW_DASHBOARD:
        # Run with dashboard
        from dashboard import SentinelDashboard
        import threading

        # Create sensors and share it
        shared_sensors = HardwareSensors()

        monitor_thread = threading.Thread(target=main,
                                          args=(shared_sensors,),
                                            daemon=True)
        monitor_thread.start()

        app = SentinelDashboard(shared_sensors)
        app.run()

    else:
        # Run background only
        main()
        