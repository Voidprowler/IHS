import tkinter as tk
from tkinter import ttk
import time
from sensors import HardwareSensors
from config import *
import threading
from PIL import Image, ImageDraw
import pystray


# ---------------------------------------------------------------------------
# Indicator states. For the three channels that raise alerts (CPU temperature,
# RAM utilisation, SSD life) the states use the SAME thresholds as the alert
# logic in main.check_alerts, so the colour and the toast always agree:
#   green = GOOD, amber = WARNING, red = CRITICAL, grey = no reading.
# GPU and SSD temperature raise no alerts; they show amber at or above the
# display limits below and never red.
# ---------------------------------------------------------------------------
GOOD, WARNING, CRITICAL = "GOOD", "WARNING", "CRITICAL"
GPU_TEMP_DISPLAY_LIMIT = 80
SSD_TEMP_DISPLAY_LIMIT = 70
STATE_COLOURS = {GOOD: '#00ff00', WARNING: '#ffaa00', CRITICAL: '#ff0000'}
NO_READING_COLOUR = '#666666'


def indicator_state(key, value):
    """GOOD, WARNING or CRITICAL for one reading, or None when there is no reading."""
    if value is None:
        return None
    if key == 'cpu_temp':
        if not value:                      # 0 means no reading, as in check_alerts
            return None
        if value >= CPU_TEMP_CRITICAL:
            return CRITICAL
        return WARNING if value >= CPU_TEMP_WARN else GOOD
    if key == 'ram_percent':
        return WARNING if value > RAM_PERCENT_LIMIT else GOOD
    if key == 'ssd_health':
        return CRITICAL if value < SSD_HEALTH_CRITICAL else GOOD
    if key == 'gpu_temp':
        return WARNING if value >= GPU_TEMP_DISPLAY_LIMIT else GOOD
    if key == 'ssd_temp':
        return WARNING if value >= SSD_TEMP_DISPLAY_LIMIT else GOOD
    return None


class SentinelDashboard:
    def __init__(self, sensors=None):
        self.window = tk.Tk()
        self.window.title("Hardware Sentinel Dashboard")
        self.window.geometry("480x420")
        self.window.resizable(False, False)
        self.window.configure(bg='#0d0d1a')

        # Use shared sensors if passed, otherwise create new
        self.sensors = sensors if sensors else HardwareSensors()
        
        # Center window on screen
        self.window.update_idletasks()
        screen_w = self.window.winfo_screenwidth()
        screen_h = self.window.winfo_screenheight()
        x = (screen_w // 2) - (480 // 2)
        y = (screen_h // 2) - (420 // 2)
        self.window.geometry(f"480x420+{x}+{y}")

        # Prevent closing from stopping the sentinel
        self.window.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)
        
        #self.sensors = HardwareSensors()
        self.running = True
        
        self.setup_ui()
        self.update_loop()

    def create_gradient(self, canvas, width, height, color1, color2):
        """Draw a top-to-bottom gradient on a canvas"""
        r1, g1, b1 = self.window.winfo_rgb(color1)
        r2, g2, b2 = self.window.winfo_rgb(color2)
        
        r1, g1, b1 = r1 // 256, g1 // 256, b1 // 256
        r2, g2, b2 = r2 // 256, g2 // 256, b2 // 256

        steps = height
        for i in range(steps):
            r = int(r1 + (r2 - r1) * i / steps)
            g = int(g1 + (g2 - g1) * i / steps)
            b = int(b1 + (b2 - b1) * i / steps)
            color = f'#{r:02x}{g:02x}{b:02x}'
            canvas.create_line(0, i, width, i, fill=color)

    def setup_ui(self):
        # Gradient background canvas
        self.canvas = tk.Canvas(self.window, width=480, height=420, 
                                highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.create_gradient(self.canvas, 480, 420, '#0d0d1a', '#1a1a3a')

        # Title
        title = tk.Label(
            self.window, 
            text="⚡ HARDWARE SENTINEL",
            font=("Segoe UI", 18, "bold"),
            bg='#0d0d1a',
            fg='#00ff00'
        )
        title.place(relx=0.5, rely=0.08, anchor='center')
        
        # Divider line
        divider = tk.Frame(self.window, bg='#1a3a1a', height=1, width=400)
        divider.place(relx=0.5, rely=0.15, anchor='center')

        # Stats frame - centered
        self.stats_frame = tk.Frame(self.window, bg='#131328')
        self.stats_frame.place(relx=0.5, rely=0.52, anchor='center')
        
        # Create stat labels
        self.labels = {}
        metrics = [
            ('CPU Temperature', 'cpu_temp', '°C', CPU_TEMP_CRITICAL),
            ('GPU Temperature', 'gpu_temp', '°C', GPU_TEMP_DISPLAY_LIMIT),
            ('Drive Temp', 'ssd_temp', '°C', SSD_TEMP_DISPLAY_LIMIT),
            ('Drive Health', 'ssd_health', '%', 20),
            ('RAM Usage', 'ram_percent', '%', RAM_PERCENT_LIMIT)
        ]
        
        for i, (name, key, unit, threshold) in enumerate(metrics):
            # Label name
            label_name = tk.Label(
                self.stats_frame,
                text=name + ":",
                font=("Segoe UI", 11),
                bg='#131328',
                fg='#aaaaaa',
                anchor='w',
                width=16
            )
            label_name.grid(row=i, column=0, sticky='w', pady=6, padx=(10, 20))
            
            # Value label
            value_label = tk.Label(
                self.stats_frame,
                text="--",
                font=("Segoe UI", 14, "bold"),
                bg='#131328',
                fg='#00ff00',
                anchor='e',
                width=8
            )
            value_label.grid(row=i, column=1, sticky='e', pady=6, padx=(0, 10))
            
            status_bar = tk.Frame(
                self.stats_frame,
                bg='#00ff00',    # Starts green, updates with readings
                width=4,
                height=24
            )
            status_bar.grid(row=i, column=2, padx=(4, 10), pady=6)

            self.labels[key] = {'label': value_label, 'unit': unit, 
                                'threshold': threshold, 'bar': status_bar}
        
        # Status bar
        self.status_label = tk.Label(
            self.window,
            text="● Monitoring Active",
            font=("Segoe UI", 9),
            bg='#131328',
            fg='#00ff00'
        )
        self.status_label.place(relx=0.5, rely=0.96, anchor='center')

        # Timestampped Update
        self.update_label = tk.Label(
            self.window,
            text="Last updated: --:--:--",
            font=("Segoe UI", 8),
            bg='#1a1a3a',
            fg='#555577'
        )
        self.update_label.place(relx=0.5, rely=0.99, anchor='s')

        # Minimize button
        minimize_btn = tk.Button(
            self.window,
            text="⬇  Minimize to Tray",
            command=self.minimize_to_tray,
            bg='#1e1e3a',
            fg='#ffffff',
            font=("Segoe UI", 10),
            relief='groove',
            bd=1,
            cursor='hand2',
            activebackground='#2a2a5a',
            activeforeground='#00ff00',
            padx=15,
            pady=5
        )
        minimize_btn.place(relx=0.5, rely=0.88, anchor='center')
        
    def get_status_color(self, value, key):
        """Return the colour for a reading, from the shared indicator states"""
        state = indicator_state(key, value)
        return STATE_COLOURS.get(state, NO_READING_COLOUR)

    def update_stats(self):
        """Fetch and display current stats"""
        stats = self.sensors.get_system_stats()
        
        for key, label_info in self.labels.items():
            value = stats.get(key)
            label = label_info['label']
            unit = label_info['unit']
            bar = label_info['bar']
            
            if value is not None:
                display_text = f"{value}{unit}"
                color = self.get_status_color(value, key)
            else:
                display_text = "N/A"
                color = '#666666'
            
            label.config(text=display_text, fg=color)
            bar.config(bg=color)
    
        self.update_label.config(
            text=f"Last updated: {time.strftime('%H:%M:%S')}"
        )

    def update_loop(self):
        """Continuous update loop"""
        if self.running:
            self.update_stats()
            self.window.after(2000, self.update_loop)  # Update every 2 seconds

    def create_tray_icon(self):
        """Load custom icon for system tray"""
        try:
            # Use the same resource path logic as DLLs
            import sys, os
            if hasattr(sys, '_MEIPASS'):
                icon_path = os.path.join(sys._MEIPASS, "Decepticons.ico")
            else:
                icon_path = os.path.join(os.path.abspath("."), "Decepticons.ico")
        
            # Load the .ico file
            if os.path.exists(icon_path):
                return Image.open(icon_path)
            else:
                raise FileNotFoundError("Icon not found, using fallback")
    
        except Exception as e:
            # Fallback: generate simple green circle
            image = Image.new('RGB', (64, 64), color='#0d0d1a')
            draw = ImageDraw.Draw(image)
            draw.ellipse([8, 8, 56, 56], fill='green', outline='white')
            return image
    
    def show_window(self, icon=None, item=None):
        """Show the window from tray"""
        if icon:
            icon.stop()
        self.window.deiconify()
        self.window.lift()

    def quit_app(self, icon=None, item=None):
        """Completely quit the application"""
        self.running = False
        if icon:
            icon.stop()
        self.window.quit()

    def minimize_to_tray(self):
        """Minimize to window to system tray"""
        self.window.withdraw()

        image = self.create_tray_icon()
        menu = pystray.Menu(
            pystray.MenuItem("Show Dashboard", self.show_window),
            pystray.MenuItem("Quit", self.quit_app)
        )

        self.tray_icon = pystray.Icon("sentinel", image, "Hardware Sentinel", menu)

        # Run tray icon in seperate thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
    
    def run(self):
        """Start the dashboard"""
        self.window.mainloop()
    
    def stop(self):
        """Stop the dashboard"""
        self.running = False
        self.window.quit()

if __name__ == "__main__":
    dashboard = SentinelDashboard()
    dashboard.run()
