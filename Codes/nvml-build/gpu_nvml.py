"""
GPU temperature through NVIDIA's NVML library, used as a fallback when
LibreHardwareMonitor does not report a GPU temperature.

NVML ships with the NVIDIA display driver (nvml.dll), so this needs no extra
Python package. It only helps on machines with an NVIDIA GPU and its driver
installed; on any other machine it simply reports "not available".

Nothing here raises: every failure means "no reading".
"""
import ctypes

NVML_SUCCESS = 0
NVML_TEMPERATURE_GPU = 0        # the GPU die sensor
_LIB_NAMES = ["nvml.dll",
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvml.dll",
              "libnvidia-ml.so.1"]


def find_library():
    for name in _LIB_NAMES:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    return None


class NvmlReader:
    """Loads NVML lazily, once, and reads every GPU's die temperature."""

    def __init__(self, lib=None):
        self._lib = None
        self._temp_fn = None
        self._handles = []
        self._tried = False
        if lib is not None:                  # injected (tests)
            self._tried = True
            self._setup(lib)

    @property
    def available(self):
        return bool(self._handles)

    @staticmethod
    def _fn(lib, *names):
        for name in names:
            fn = getattr(lib, name, None)
            if fn is not None:
                return fn
        return None

    def _setup(self, lib):
        try:
            init = self._fn(lib, "nvmlInit_v2", "nvmlInit")
            count_fn = self._fn(lib, "nvmlDeviceGetCount_v2", "nvmlDeviceGetCount")
            handle_fn = self._fn(lib, "nvmlDeviceGetHandleByIndex_v2", "nvmlDeviceGetHandleByIndex")
            temp_fn = self._fn(lib, "nvmlDeviceGetTemperature")
            if not all((init, count_fn, handle_fn, temp_fn)):
                return
            if init() != NVML_SUCCESS:
                return
            count = ctypes.c_uint()
            if count_fn(ctypes.byref(count)) != NVML_SUCCESS:
                return
            handles = []
            for i in range(count.value):
                handle = ctypes.c_void_p()
                if handle_fn(i, ctypes.byref(handle)) == NVML_SUCCESS:
                    handles.append(handle)
            self._lib, self._temp_fn, self._handles = lib, temp_fn, handles
        except Exception:
            self._handles = []

    def temperatures(self):
        """Die temperature (C) of every GPU that answers."""
        if not self._tried:
            self._tried = True
            lib = find_library()
            if lib is not None:
                self._setup(lib)
        out = []
        for handle in self._handles:
            value = ctypes.c_uint()
            try:
                if (self._temp_fn(handle, NVML_TEMPERATURE_GPU, ctypes.byref(value)) == NVML_SUCCESS
                        and value.value > 0):
                    out.append(float(value.value))
            except Exception:
                continue
        return out

    def read(self):
        """Hottest GPU's temperature, or None."""
        temps = self.temperatures()
        return max(temps) if temps else None
