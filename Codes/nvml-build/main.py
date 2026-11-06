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
from telemetry_log import TelemetryLog
from datetime import datetime
from config import *


# Does user want to display Dashboard
SHOW_DASHBOARD = '--dashboard' in sys.argv 

def check_alerts(alerts, stats):
    """
    Evaluate one polling cycle and return a list of event strings for the
    telemetry log, e.g. "cpu_temp:WARNING:sent" or "cpu_temp:recovered".

    For each channel: a breach calls alerts.notify() (which applies the
    channel + severity rate limiting); a valid Good reading calls
    alerts.report_ok() so the channel can recover. A missing reading
    (None) does neither, so an unavailable sensor can neither raise an
    alert nor reset a cooldown.
    """
    events = []

    def breach(channel, severity, title, message):
        alerts.notify(title, message, channel=channel, severity=severity)
        events.append(f"{channel}:{severity}:{alerts.last_outcome}")

    def good(channel):
        if alerts.report_ok(channel):
            events.append(f"{channel}:recovered")

    # CPU temperature. 0 or None means no valid reading (shown as "Locked").
    cpu = stats['cpu_temp']
    if cpu:
        if cpu >= CPU_TEMP_CRITICAL:
            breach("cpu_temp", "CRITICAL", "\U0001F525 CPU CRITICAL",
                   f"Temperature hit {cpu}\u00b0C\n"
                   "\u2192 Check if fans are running\n"
                   "\u2192 Elevate Laptop\n"
                   "\u2192 Close resource-heavy programs")
        elif cpu >= CPU_TEMP_WARN:
            breach("cpu_temp", "WARNING", "\u26a0\ufe0f CPU HOT",
                   f"Temperature hit {cpu}\u00b0C\n"
                   "\u2192 Close heavy programs\n"
                   "\u2192 Check ventilation")
        else:
            good("cpu_temp")

    # RAM utilisation
    ram = stats['ram_percent']
    if ram is not None:
        if ram > RAM_PERCENT_LIMIT:
            breach("ram", "WARNING", "\u26a0\ufe0f RAM WARNING",
                   f"Memory usage is high: {ram}%\n"
                   "\u2192 Open Task Manager (Ctrl+Shift+Esc keys)\n"
                   "\u2192 Close unnecessary programs")
        else:
            good("ram")

    # SSD life remaining. 0% is a valid (worst) reading, so test for None.
    ssd = stats['ssd_health']
    if ssd is not None:
        if ssd < SSD_HEALTH_CRITICAL:
            drive = stats.get('ssd_health_drive')
            which = f"{drive}\n" if drive else ""
            breach("ssd_health", "CRITICAL", "\U0001F6D1 SSD CRITICAL",
                   f"{which}SSD Life Remaining: {ssd}%\n"
                   "\u2192 BACKUP YOUR DATA NOW\n"
                   "\u2192 Plan drive replacement")
        else:
            good("ssd_health")

    return events


def main(sensors=None):
    if sensors is None:
        sensors = HardwareSensors()  # Only creates if not passed in
    alerts = AlertSystem()
    telemetry = TelemetryLog(TELEMETRY_FILE) if LOG_TELEMETRY else None
    if telemetry is not None:
        if telemetry.active:
            logging.info(f"TELEMETRY FILE: {telemetry.path}")
            if telemetry.path != telemetry.requested_path:
                print(f"Note: {telemetry.requested_path} was unavailable; "
                      f"this run logs to {telemetry.path}")
        else:
            logging.error("TELEMETRY DISABLED for this run")
            print("WARNING: telemetry log is disabled for this run (see sentinel_log.txt)")
    
    # Initial check to log what is found at startup
    initial_stats = sensors.get_system_stats()
    logging.info(f"RAW STARTUP STATS: {initial_stats}")
    alerts.log_system_profile(initial_stats)
    for d in initial_stats.get('ssd_drives', []):
        logging.info(f"SSD DETECTED: {d['name']} health={d['health']} temp={d['temp']}")
    logging.info(f"SENSOR SOURCES: cpu_temp = {initial_stats.get('cpu_source')}; "
                 f"gpu_temp = {initial_stats.get('gpu_source')}")
    
    print("\n" + "="*50)
    print("      INTELLIGENT HARDWARE SENTINEL ACTIVE      ")
    print("="*50 + "\n")


    loop_count = 0
    try:
        while True:
            loop_count += 1
            stats = sensors.get_system_stats()
            read_time = datetime.now().isoformat(timespec='milliseconds')
            
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
            ssd_h = f"Health: {stats['ssd_health']}%" if stats['ssd_health'] is not None else "Health: N/A"
            ram = f"RAM: {stats['ram_percent']}%"

            # Print status line (using \r to overwrite the same line)
            status_line = f"| {cpu} | {gpu} | {ssd_t} | {ssd_h} | {ram} |"
            if not hasattr(sys, '_MEIPASS'):
                print(status_line, end="\r")

            # 3. Alert Logic (see check_alerts)
            t0 = time.perf_counter()
            events = check_alerts(alerts, stats)
            alert_ms = (time.perf_counter() - t0) * 1000
            if telemetry:
                telemetry.write(read_time, loop_count, stats, events, alert_ms)

            time.sleep(SCAN_INTERVAL)

    except KeyboardInterrupt:
        print("\n\nSentinel shutting down safely...")
    except Exception as e:
        logging.error(f"FATAL ERROR: {e}", exc_info=True)
        raise
    finally:
        if telemetry:
            telemetry.close()


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
        