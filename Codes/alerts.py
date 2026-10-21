#from plyer import notification
import logging
import time

logging.basicConfig(
    filename='sentinel_log.txt', 
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

class AlertSystem:
    def __init__(self):
        self.last_alerts = {}  # Per-alert tracking, NOT global

    def log_system_profile(self, data):
        """Logs the hardware detected at startup."""
        detected = [k for k, v in data.items() if v is not None]
        msg = f"SENTINEL STARTUP: Monitoring {', '.join(detected).upper()}"
        logging.info(msg)
        print(f"--- {msg} ---")

    def notify(self, title, message):
        """Send notification with smart per-alert rate limiting"""

        # alert type: different alerts don't block each other
        alert_key = f"{title}:{message[:50]}"

        current_time = time.time()
        last_time = self.last_alerts.get(alert_key, 0)

        # Only block if THIS SAME alert fired in last 60s
        if current_time - last_time <= 60:
            return False
         
        try:
            from winotify import Notification, audio

            toast = Notification(app_id="Intelligent Hardware Sentinel",
                                title=title,
                                msg=message)
            toast.set_audio(audio.Default, loop=False)
            toast.show()

            # Update only THIS alert's timestamp
            self.last_alerts[alert_key] = current_time
            
            clean_message = message.replace('\n', ' | ')
            full_message = f"{title}: {clean_message}"
            logging.warning(full_message)
            return True
        
        # Alert sys failed
        except Exception as e:
            clean_message = message.replace('\n', ' | ')
            full_message = f"{title}: {clean_message}"
            logging.warning(f"{full_message} (toast failed: {repr(e)})")
            return False