import unittest

from gpu_nvml import NvmlReader
import compare_gpu_sources as cmp


class FakeLib:
    """Stands in for nvml.dll. byref() arguments expose the target as ._obj."""

    def __init__(self, temps, init_rc=0, temp_rcs=None):
        self.temps, self.init_rc = temps, init_rc
        self.temp_rcs = temp_rcs or [0] * len(temps)

    def nvmlInit_v2(self):
        return self.init_rc

    def nvmlDeviceGetCount_v2(self, ref):
        ref._obj.value = len(self.temps)
        return 0

    def nvmlDeviceGetHandleByIndex_v2(self, index, ref):
        ref._obj.value = 1000 + index
        return 0

    def nvmlDeviceGetTemperature(self, handle, kind, ref):
        i = handle.value - 1000
        assert kind == 0                                   # the GPU-die sensor
        ref._obj.value = self.temps[i]
        return self.temp_rcs[i]


class NvmlReaderTests(unittest.TestCase):

    def test_reads_the_hottest_of_several_gpus(self):
        r = NvmlReader(lib=FakeLib([48, 61]))
        self.assertTrue(r.available)
        self.assertEqual(r.temperatures(), [48.0, 61.0])
        self.assertEqual(r.read(), 61.0)

    def test_a_gpu_that_errors_is_skipped(self):
        r = NvmlReader(lib=FakeLib([48, 61], temp_rcs=[0, 15]))
        self.assertEqual(r.temperatures(), [48.0])

    def test_failed_init_means_not_available_and_no_reading(self):
        r = NvmlReader(lib=FakeLib([55], init_rc=1))
        self.assertFalse(r.available)
        self.assertIsNone(r.read())

    def test_no_gpus_means_no_reading(self):
        r = NvmlReader(lib=FakeLib([]))
        self.assertIsNone(r.read())

    def test_zero_is_not_a_reading(self):
        self.assertIsNone(NvmlReader(lib=FakeLib([0])).read())

    def test_library_missing_functions_is_handled(self):
        self.assertIsNone(NvmlReader(lib=object()).read())

    def test_without_the_library_it_reports_nothing_and_does_not_raise(self):
        r = NvmlReader()                  # real lookup: no NVIDIA driver here (or a real one: still fine)
        r.read()                          # must not raise
        r.read()


class CompareScriptTests(unittest.TestCase):

    def test_summarise(self):
        r = cmp.summarise([(60.0, 60.0), (62.0, 61.0), (65.0, 61.0)])
        self.assertEqual(r["n"], 3)
        self.assertAlmostEqual(r["mean_abs_diff"], (0 + 1 + 4) / 3)
        self.assertEqual(r["max_abs_diff"], 4.0)
        self.assertAlmostEqual(r["within_2c"], 2 / 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
