import csv
import logging
import os
from datetime import datetime


class TelemetryLog:
    """
    One CSV row per polling cycle: timestamp the readings were taken,
    loop number, every channel's value, the alert outcomes for that cycle,
    how long the alert evaluation took (including toast display), and a
    per-drive SSD summary.

    This is the raw evidence behind detection, latency and suppression
    counts. It never raises, and a locked file never costs you a session:

      * a file written with different columns is renamed aside, not mixed into;
      * if the file cannot be used (typically because it is open in Excel,
        which locks it), this run writes to a new time-stamped file instead;
      * only if that also fails is telemetry disabled, and that is reported.
    """

    FIELDS = ["timestamp", "loop", "cpu_temp", "gpu_temp", "ssd_temp",
              "ssd_health", "ram_percent", "alert_events", "alert_ms", "ssd_detail"]
    CHANNELS = ["cpu_temp", "gpu_temp", "ssd_temp", "ssd_health", "ram_percent"]

    def __init__(self, path):
        self.requested_path = path
        self.path = path
        self._file = None
        self._writer = None
        base, ext = os.path.splitext(path)
        fallback = f"{base}_{datetime.now():%Y%m%d_%H%M%S}{ext}"
        for candidate in (path, fallback):
            try:
                self._open(candidate, retire_old=(candidate == path))
            except OSError as e:
                logging.warning(f"Telemetry: cannot use {candidate}: {e!r}")
                continue
            self.path = candidate
            if candidate != path:
                logging.warning(f"Telemetry: {path} is locked or unusable (often because it is "
                                f"open in Excel). This run is writing to {candidate} instead.")
            return
        logging.error(f"Telemetry log disabled: neither {path} nor {fallback} could be opened")
        self._file = self._writer = None

    @property
    def active(self):
        return self._writer is not None

    def _open(self, path, retire_old):
        if retire_old:
            self._retire_old_layout(path)
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        f = open(path, "a", newline="", encoding="utf-8")
        try:
            writer = csv.writer(f)
            if is_new:
                writer.writerow(self.FIELDS)
                f.flush()
        except OSError:
            f.close()
            raise
        self._file, self._writer = f, writer

    def _retire_old_layout(self, path):
        """A file written with different columns is renamed, never mixed into."""
        if not os.path.exists(path) or os.path.getsize(path) == 0:
            return
        with open(path, encoding="utf-8-sig") as f:
            first = f.readline().strip()
        if first != ",".join(self.FIELDS):
            stamp = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y%m%d_%H%M%S")
            base, ext = os.path.splitext(path)
            os.replace(path, f"{base}.old_{stamp}{ext}")
            logging.warning(f"Telemetry file had a different column layout; "
                            f"kept as {base}.old_{stamp}{ext} and started a new file")

    @staticmethod
    def format_drives(drives):
        """'name=health/temp|name=health/temp' (empty value = no reading)."""
        def f(v):
            return "" if v is None else f"{v:g}"
        return "|".join(f"{d['name']}={f(d.get('health'))}/{f(d.get('temp'))}" for d in drives or [])

    def write(self, timestamp, loop, stats, events, alert_ms):
        """events: list of strings such as 'cpu_temp:WARNING:sent'."""
        if self._writer is None:
            return
        row = [timestamp, loop]
        row += ["" if stats.get(k) is None else stats[k] for k in self.CHANNELS]
        row += [";".join(events), f"{alert_ms:.1f}", self.format_drives(stats.get("ssd_drives"))]
        try:
            self._writer.writerow(row)
            self._file.flush()
        except (OSError, ValueError) as e:
            logging.error(f"Telemetry log disabled after write failure: {e!r}")
            self.close()

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
        self._file = self._writer = None
