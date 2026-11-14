import sys
import types
import unittest
from unittest import mock

# sensors.py needs pythonnet (clr) and psutil; stub them only if missing,
# so these tests also run on a machine without the hardware bridge.
for _mod in ("clr", "psutil"):
    try:
        __import__(_mod)
    except ImportError:
        sys.modules[_mod] = types.ModuleType(_mod)

import sensors as sensors_module
from sensors import HardwareSensors


class FakeSensor:
    def __init__(self, stype, name, value):
        self.SensorType, self.Name, self.Value = stype, name, value


class FakeHW:
    def __init__(self, htype, name, ident, sensors, drive_letters=None):
        self.HardwareType, self.Name, self.Identifier = htype, name, ident
        self.Sensors, self.SubHardware = sensors, []
        if drive_letters is not None:
            self.DriveInfos = [types.SimpleNamespace(Name=l + "\\") for l in drive_letters]

    def Update(self):
        pass


def drive(name, ident, life, temp, letters=None, life_name="Remaining Life"):
    sens = [FakeSensor("Temperature", "Temperature", temp),
            FakeSensor("Temperature", "Warning Temperature", 83),      # must be ignored
            FakeSensor("Level", life_name, life)]
    return FakeHW("Storage", name, ident, sens, letters)


class FakeNvml:
    def __init__(self, temp=None):
        self.temp, self.calls = temp, 0

    def read(self):
        self.calls += 1
        return self.temp


def make(hardware):
    s = HardwareSensors.__new__(HardwareSensors)        # skip the DLL bridge
    s.ohm_enabled, s.wmi_fallback, s._drive_labels = True, True, {}
    s.computer = types.SimpleNamespace(Hardware=hardware)
    s.get_ram_usage = lambda: 50.0
    s.get_cpu_temp_wmi = lambda: None
    s.nvml = FakeNvml()
    return s


class StorageTests(unittest.TestCase):

    def setUp(self):
        sensors_module.DEBUG_SENSORS = False            # no debug-log writes in tests

    def test_reports_the_worst_drive_regardless_of_order(self):
        c = ("WDC PC SN730 SDBPNTY-512G", "/nvme/0", 85, 53)
        d = ("WDC PC SN730 SDBPNTY-1T00", "/nvme/1", 99, 49)
        for order in ([c, d], [d, c]):
            data = make([drive(*x) for x in order]).get_system_stats()
            self.assertEqual(data["ssd_health"], 85.0)
            self.assertEqual(data["ssd_health_drive"], "WDC PC SN730 SDBPNTY-512G")
            self.assertEqual(data["ssd_temp"], 53.0)            # hottest drive
            self.assertEqual(len(data["ssd_drives"]), 2)

    def test_zero_percent_life_is_kept_not_discarded(self):
        data = make([drive("WornOut", "/nvme/0", 0, 40)]).get_system_stats()
        self.assertEqual(data["ssd_health"], 0.0)

    def test_zero_is_still_no_reading_for_temperatures(self):
        data = make([drive("D", "/nvme/0", 90, 0)]).get_system_stats()
        self.assertIsNone(data["ssd_temp"])
        self.assertEqual(data["ssd_health"], 90.0)

    def test_threshold_temperatures_are_ignored(self):
        data = make([drive("D", "/nvme/0", 90, 41)]).get_system_stats()
        self.assertEqual(data["ssd_temp"], 41.0)                # not the 83 'Warning Temperature'

    def test_drive_without_a_health_sensor_does_not_hide_the_other(self):
        no_health = FakeHW("Storage", "NoSmart", "/hdd/0", [FakeSensor("Temperature", "Temperature", 35)])
        data = make([no_health, drive("D", "/nvme/0", 70, 44)]).get_system_stats()
        self.assertEqual(data["ssd_health"], 70.0)
        self.assertEqual(data["ssd_temp"], 44.0)
        self.assertEqual(len(data["ssd_drives"]), 2)

    def test_no_storage_hardware_gives_none(self):
        data = make([]).get_system_stats()
        self.assertIsNone(data["ssd_health"])
        self.assertIsNone(data["ssd_health_drive"])
        self.assertEqual(data["ssd_drives"], [])

    def test_two_identical_models_stay_separate(self):
        a = drive("SameModel", "/nvme/0", 60, 40)
        b = drive("SameModel", "/nvme/1", 95, 42)
        data = make([a, b]).get_system_stats()
        self.assertEqual(len(data["ssd_drives"]), 2)
        self.assertEqual(data["ssd_health"], 60.0)

    def test_drive_letters_are_added_when_available_and_optional_otherwise(self):
        with_letters = drive("WDC-512G", "/nvme/0", 85, 53, letters=["C:"])
        without = drive("WDC-1T00", "/nvme/1", 99, 49)
        data = make([with_letters, without]).get_system_stats()
        names = [d["name"] for d in data["ssd_drives"]]
        self.assertEqual(names, ["WDC-512G (C:)", "WDC-1T00"])

    def test_cpu_and_gpu_readings_are_unchanged(self):
        cpu = FakeHW("Cpu", "Intel", "/cpu/0", [FakeSensor("Temperature", "CPU Package", 61.2)])
        gpu = FakeHW("GpuNvidia", "RTX", "/gpu/0", [FakeSensor("Temperature", "GPU Core", 58.7)])
        data = make([cpu, gpu]).get_system_stats()
        self.assertEqual((data["cpu_temp"], data["gpu_temp"]), (61.2, 58.7))


    def test_nvml_is_used_when_lhm_has_no_gpu_temperature(self):
        s = make([drive("D", "/nvme/0", 90, 40)])
        s.nvml = FakeNvml(61.0)
        data = s.get_system_stats()
        self.assertEqual(data["gpu_temp"], 61.0)
        self.assertTrue(data["gpu_source"].startswith("NVML"))

    def test_nvml_is_not_consulted_when_lhm_reports_the_gpu(self):
        gpu = FakeHW("GpuNvidia", "NVIDIA RTX", "/gpu/0", [FakeSensor("Temperature", "GPU Core", 58.7)])
        s = make([gpu])
        s.nvml = FakeNvml(99.0)
        data = s.get_system_stats()
        self.assertEqual(data["gpu_temp"], 58.7)
        self.assertEqual(s.nvml.calls, 0)
        self.assertEqual(data["gpu_source"], "LibreHardwareMonitor: NVIDIA RTX / GPU Core")

    def test_force_flag_uses_nvml_even_when_lhm_has_a_value(self):
        gpu = FakeHW("GpuNvidia", "NVIDIA RTX", "/gpu/0", [FakeSensor("Temperature", "GPU Core", 58.7)])
        s = make([gpu])
        s.nvml = FakeNvml(57.0)
        with mock.patch.object(sensors_module, "GPU_FORCE_NVML", True, create=True):
            data = s.get_system_stats()
        self.assertEqual(data["gpu_temp"], 57.0)
        self.assertTrue(data["gpu_source"].startswith("NVML"))

    def test_no_gpu_anywhere_gives_none_and_no_source(self):
        data = make([]).get_system_stats()
        self.assertIsNone(data["gpu_temp"])
        self.assertIsNone(data["gpu_source"])

    def test_cpu_source_names_the_sensor_used(self):
        cpu = FakeHW("Cpu", "Intel Core i7", "/cpu/0", [FakeSensor("Temperature", "CPU Package", 61.2)])
        data = make([cpu]).get_system_stats()
        self.assertEqual(data["cpu_source"], "LibreHardwareMonitor: Intel Core i7 / CPU Package")

    def test_wmi_fallback_is_labelled_as_a_different_measurement(self):
        s = make([])
        s.get_cpu_temp_wmi = lambda: 41.0
        data = s.get_system_stats()
        self.assertEqual(data["cpu_temp"], 41.0)
        self.assertIn("ACPI thermal zone", data["cpu_source"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
