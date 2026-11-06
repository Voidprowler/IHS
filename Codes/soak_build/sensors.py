import clr
#import os
#import sys
import psutil
from config import *

import sys, os

def get_resource_path(rel):
    base = getattr(sys, "_MEIPASS", os.path.abspath("."))
    return os.path.join(base, rel)


class HardwareSensors:
    def __init__(self):
        self.ohm_enabled = False
        self.wmi_fallback = False   # set early so it always exists
        self._drive_labels = {}     # drive key -> display label (cached)
        try:
            dll_path = get_resource_path("LibreHardwareMonitorLib.dll")
            if os.path.exists(dll_path):
                clr.AddReference(dll_path)
                from LibreHardwareMonitor.Hardware import Computer # type: ignore
                self.computer = Computer()
                self.computer.IsCpuEnabled = True
                self.computer.IsGpuEnabled = True
                self.computer.IsStorageEnabled = True
                self.computer.IsMemoryEnabled = True
                self.computer.IsMotherboardEnabled = True 
                self.computer.Open()
                self.ohm_enabled = True
                self.wmi_fallback = False  # Tracks if WMI is being used
                
            else:
                with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"[HW BRIDGE ERROR] DLL not found at: {dll_path}\n")
        except Exception as e:
            #print(f"Hardware Bridge Error: {e}")
            self.ohm_enabled = False
            with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                f.write(f"[HW BRIDGE ERROR] {repr(e)}\n")

    def get_cpu_temp_wmi(self):
        """Fallback CPU temperature via WMI when LibreHardwareMonitor returns None"""
        try:
            import wmi
            w = wmi.WMI(namespace="root\\wmi")
            temps = w.MSAcpi_ThermalZoneTemperature()
            if temps:
                # WMI returns temperature in tenths of Kelvin
                kelvin = temps[0].CurrentTemperature / 10.0
                celsius = round(kelvin - 273.15, 1)
                return celsius
        except Exception as e:
            with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                f.write(f"[WMI FALLBACK ERROR] {e}\n")
        return None


    def get_ram_usage(self):
        return psutil.virtual_memory().percent

    def _drive_entry(self, drives, hw):
        """Return (creating if needed) the record for one physical storage device."""
        try:
            key = str(hw.Identifier)          # unique per device, even for two identical models
        except Exception:
            key = str(hw.Name)
        if key not in drives:
            if key not in self._drive_labels:
                name = str(hw.Name or key)
                letters = ""
                try:                          # best effort: e.g. "C:" - falls back to the model name
                    letters = ", ".join(str(d.Name).rstrip("\\") for d in hw.DriveInfos)
                except Exception:
                    pass
                self._drive_labels[key] = f"{name} ({letters})" if letters else name
            drives[key] = {"name": self._drive_labels[key], "health": None, "temp": None}
        return drives[key]

    def get_system_stats(self):
        data = {'cpu_temp': None, 'gpu_temp': None,
                'ssd_health': None, 'ssd_temp': None,
                'ssd_health_drive': None, 'ssd_drives': [],
                'ram_percent': self.get_ram_usage()}
        
        if not self.ohm_enabled:
            return data

        if DEBUG_SENSORS:
            with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                f.write("\n--- SENSOR ENUMERATION ---\n")

                # SAFE list conversion first
                try:
                    debug_hardware_list = list(self.computer.Hardware) if self.computer.Hardware else []
                except:
                    debug_hardware_list = []

                for hw in debug_hardware_list:  # ← Iterate safe list, not self.computer.Hardware
                    try:
                        hw.Update()
                        f.write(f"[HW] {hw.HardwareType} {hw.Name}\n")

                        if str(hw.HardwareType).lower() == "cpu":
                            f.write(f"\n[CPU] {hw.Name}\n")

                            try:
                                sensors = list(hw.Sensors) if hw.Sensors else []
                            except:
                                sensors = []

                            for s in sensors:
                                try:
                                    if str(s.SensorType).lower() == "temperature":
                                        f.write(f"  {s.Name}: {s.Value}\n")
                                except:
                                    pass

                            try:
                                sub_hardware = list(hw.SubHardware) if hw.SubHardware else []
                            except:
                                sub_hardware = []

                            for sub in sub_hardware:
                                try:
                                    f.write(f"[CPU SUB] {sub.Name}\n")
                                    sub_sensors = list(sub.Sensors) if sub.Sensors else []
                                    for s in sub_sensors:
                                        if str(s.SensorType).lower() == "temperature":
                                            f.write(f"  {s.Name}: {s.Value}\n")
                                except Exception as e:
                                    f.write(f"[CPU SUB ERROR] {e}\n")

                    except Exception as e:
                        f.write(f"[DEBUG ERROR] {e}\n")



        
        drives = {}   # one record per physical storage device

        def handle_hw(hw):
            nonlocal data
            try:
                hw.Update()
            except:
                return #*
            
            hw_type = str(hw.HardwareType).lower()

            if DEBUG_SENSORS and "cpu" in hw_type:
                with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                    try:
                        debug_sensors = list(hw.Sensors) if hw.Sensors else []
                        for s in debug_sensors:
                            if str(s.SensorType).lower() == "temperature":
                                f.write(f"[CPU TEMP] {s.Name}: {s.Value}\n")
                    except:
                        f.write("[CPU TEMP] Sensor enumeration failed\n")

            # Safe sensor iteration
            try:
                sensors = list(hw.Sensors) if hw.Sensors else []
            except:
                sensors = []


            drive = self._drive_entry(drives, hw) if "storage" in hw_type else None

            for sensor in sensors:
                try:
                    s_type = str(sensor.SensorType).lower()
                    s_name = (sensor.Name or "").lower()
                    val = sensor.Value
                    if val is None:
                        continue
                    is_life = (drive is not None and s_type == "level"
                               and any(k in s_name for k in ["life", "drive rating", "health", "remaining"]))
                    # 0 is a legitimate "worn out" reading for drive life; for every
                    # other sensor a value of 0 or less means "no reading".
                    if val < 0 or (val == 0 and not is_life):
                        continue

                    if s_type == "temperature":
                        if "cpu" in hw_type:
                            if data['cpu_temp'] is None or any(k in s_name for k in
                                                            ["package", "core max", "tctl", "tdie", "die"]):
                                data['cpu_temp'] = round(float(val), 1)

                        #elif any(g in hw_type for g in ["gpunvidia", "gpuati", "gpuintel"]):
                        elif "gpu" in hw_type:  # Catches all GPU types
                            data['gpu_temp'] = round(float(val), 1)

                        elif drive is not None:
                            if any(k in s_name for k in ["warning", "critical", "threshold"]): continue
                            current_val = round(float(val), 1)
                            if drive["temp"] is None or current_val > drive["temp"]:
                                drive["temp"] = current_val

                    if is_life:
                        life = round(float(val), 1)
                        if drive["health"] is None or life < drive["health"]:
                            drive["health"] = life

                except:
                    continue #*

        try:
            hardware_list = list(self.computer.Hardware) if self.computer.Hardware else []
        except:
            hardware_list = []

        for hardware in hardware_list:
            handle_hw(hardware)

            try:
                sub_hardware = list(hardware.SubHardware) if hardware.SubHardware else []
            except:
                sub_hardware = []

            for sub in sub_hardware:
                handle_hw(sub)
        
        # Storage: report every drive, but summarise for alerting with the WORST
        # health (so whichever drive is failing triggers the alert) and the
        # HOTTEST temperature. Enumeration order no longer matters.
        data['ssd_drives'] = [dict(d) for d in drives.values()]
        with_health = [d for d in drives.values() if d["health"] is not None]
        if with_health:
            worst = min(with_health, key=lambda d: (d["health"], d["name"]))
            data['ssd_health'] = worst["health"]
            data['ssd_health_drive'] = worst["name"]
        temps = [d["temp"] for d in drives.values() if d["temp"] is not None]
        if temps:
            data['ssd_temp'] = max(temps)

        # WMI fallback if LibreHardwareMonitor couldn't read CPU temp
        if data['cpu_temp'] is None:
            data['cpu_temp'] = self.get_cpu_temp_wmi()
            if data['cpu_temp'] and not self.wmi_fallback:
                self.wmi_fallback = True
                with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"[CPU TEMP] WMI fallback active: {data['cpu_temp']}°C\n")

                
        return data