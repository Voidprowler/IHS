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
        try:
            dll_path = get_resource_path("LibreHardwareMonitorLib.dll")
            if os.path.exists(dll_path):
                clr.AddReference(dll_path)
                from LibreHardwareMonitor.Hardware import Computer
                self.computer = Computer()
                self.computer.IsCpuEnabled = True
                self.computer.IsGpuEnabled = True
                self.computer.IsStorageEnabled = True
                self.computer.IsMemoryEnabled = True
                self.computer.IsMotherboardEnabled = True 
                self.computer.Open()
                self.ohm_enabled = True
                
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

    def get_system_stats(self):
        data = {'cpu_temp': None, 'gpu_temp': None,
                'ssd_health': None, 'ssd_temp': None, 
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
                        for s in hw.Sensors:
                            if str(s.SensorType).lower() == "temperature":
                                f.write(f"[CPU TEMP] {s.Name}: {s.Value}\n")
                    except:
                        f.write("[CPU TEMP] Sensor enumeration failed\n")

            # Safe sensor iteration
            try:
                sensors = list(hw.Sensors) if hw.Sensors else []
            except:
                sensors = []


            for sensor in sensors:
                try:
                    s_type = str(sensor.SensorType).lower()
                    val = sensor.Value 
                    if val is None or val <= 0: continue

                    if s_type == "temperature":
                        s_name = (sensor.Name or "").lower()
                        if "cpu" in hw_type:
                            if data['cpu_temp'] is None or any(k in s_name for k in 
                                                            ["package", "core max", "tctl", "tdie", "die"]):
                                data['cpu_temp'] = round(float(val), 1)

                        elif any(g in hw_type for g in ["gpunvidia", "gpuati", "gpuintel"]):
                            data['gpu_temp'] = round(float(val), 1)

                        elif "storage" in hw_type:
                            if any(k in s_name for k in ["warning", "critical", "threshold"]): continue
                            current_val = round(float(val), 1)
                            if data['ssd_temp'] is None or current_val > data['ssd_temp']:
                                data['ssd_temp'] = current_val

                    if "storage" in hw_type and s_type == "level":
                        if "life" in (sensor.Name or "").lower():
                            data['ssd_health'] = round(float(val), 1)

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
        
        # WMI fallback if LibreHardwareMonitor couldn't read CPU temp
        if data['cpu_temp'] is None:
            with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                f.write("[CPU TEMP] LibreHardwareMonitor returned None, trying WMI fallback...\n")
            data['cpu_temp'] = self.get_cpu_temp_wmi()
            if data['cpu_temp']:
                with open("sentinel_debug.log", "a", encoding="utf-8") as f:
                    f.write(f"[CPU TEMP] WMI fallback successful: {data['cpu_temp']}°C\n")

                
        return data