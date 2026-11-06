# Thresholds
CPU_TEMP_WARN = 72
CPU_TEMP_CRITICAL = 82
RAM_PERCENT_LIMIT = 90
DISK_PERCENT_LIMIT = 95
SSD_HEALTH_CRITICAL = 20   # percent life remaining; below this = CRITICAL

# Feature Toggles
CHECK_NETWORK = True
CHECK_SSD_HEALTH = True
SCAN_INTERVAL = 2 # Seconds between checks

DEBUG_SENSORS = True

# Telemetry audit log: one CSV row per polling cycle (readings + alert outcomes)
LOG_TELEMETRY = True
TELEMETRY_FILE = 'sentinel_telemetry.csv'
