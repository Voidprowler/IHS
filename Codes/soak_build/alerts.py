import logging
import time
from collections import defaultdict

logging.basicConfig(
    filename='sentinel_log.txt',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

MONITORED_CHANNELS = ("cpu_temp", "gpu_temp", "ssd_temp", "ssd_health", "ram_percent")

# GOOD is never passed to notify(); it exists so callers can compare ranks.
SEVERITY_RANK = {"GOOD": 0, "WARNING": 1, "CRITICAL": 2}


class AlertSystem:
    """
    Alert delivery with per-channel, per-severity rate limiting.

    The cooldown is keyed on WHAT is being reported (the channel, e.g.
    "cpu_temp", and its severity), never on the message text. The measured
    value can change on every reading without creating a new key.

    Rules, per channel (the cooldown is tracked per channel AND severity):
      - First breach: toast immediately.
      - A more severe level than the current one that has not alerted yet
        in this episode (escalation): toast immediately.
      - Any level that already alerted within the cooldown: suppressed.
        This includes a RE-escalation: a reading that dips from CRITICAL to
        WARNING and back to CRITICAL does not buy a fresh CRITICAL toast.
      - A level that alerted longer ago than the cooldown: toast again
        (reminder).
      - A toast at one severity also counts as a recent notice for every
        LOWER severity, so a CRITICAL toast is not followed by a WARNING
        toast about the same condition a few seconds later.
      - Less severe than the current level (de-escalation): logged only,
        no toast.
      - Channel returns to normal for `clear_after` consecutive readings
        (reported via report_ok): state is cleared, so a renewed breach
        toasts immediately.
      - Missing/stale readings: do NOT call report_ok. State is left
        untouched, so an unreliable sensor cannot reset the cooldown.
    """

    COOLDOWN_SECONDS = 60
    CLEAR_AFTER_OK_READINGS = 3
    LOG_SUPPRESSED = True   # one INFO line per suppressed alert (audit trail)

    def __init__(self, cooldown=COOLDOWN_SECONDS,
                 clear_after=CLEAR_AFTER_OK_READINGS,
                 clock=time.monotonic):
        self.cooldown = cooldown
        self.clear_after = clear_after
        self._clock = clock          # injectable so tests need no real waiting
        # channel -> {"severity": str, "sent_at": {severity: time}, "ok_streak": int}
        self._state = {}
        # what happened to the most recent notify(): sent / failed /
        # suppressed / deescalated (read by main.py for the telemetry log)
        self.last_outcome = None
        # channel -> counters, for the test matrix and results chapter
        self.stats = defaultdict(lambda: {
            "sent": 0, "suppressed": 0, "failed": 0,
            "deescalated": 0, "recovered": 0,
        })

    # ------------------------------------------------------------------
    def log_system_profile(self, data):
        """Logs the hardware detected at startup."""
        detected = [k for k, v in data.items()
                    if v is not None and k in MONITORED_CHANNELS]
        msg = f"SENTINEL STARTUP: Monitoring {', '.join(detected).upper()}"
        logging.info(msg)
        print(f"--- {msg} ---")

    # ------------------------------------------------------------------
    def notify(self, title, message, *, channel, severity):
        """
        Request an alert. Returns True only if a toast was shown.

        channel  -- stable identifier, e.g. "cpu_temp", "gpu_temp", "ram"
        severity -- "WARNING" or "CRITICAL"
        """
        severity = str(severity).upper()
        if severity not in SEVERITY_RANK or severity == "GOOD":
            raise ValueError(f"severity must be WARNING or CRITICAL, got {severity!r}")

        now = self._clock()
        counters = self.stats[channel]
        clean_message = message.replace('\n', ' | ')
        state = self._state.get(channel)

        if state is not None:
            state["ok_streak"] = 0   # a breach reading interrupts recovery
            old_rank = SEVERITY_RANK[state["severity"]]
            new_rank = SEVERITY_RANK[severity]

            if new_rank < old_rank:
                # De-escalation: record it, do not toast. The per-severity
                # cooldowns are kept, so coming back up does not reset them.
                previous = state["severity"]
                state["severity"] = severity
                counters["deescalated"] += 1
                logging.info(
                    f"DE-ESCALATED [{channel}] {previous} -> {severity}: "
                    f"{title}: {clean_message}"
                )
                self.last_outcome = "deescalated"
                return False

            state["severity"] = severity
            last = state["sent_at"].get(severity)
            if last is not None and (now - last) < self.cooldown:
                counters["suppressed"] += 1
                if self.LOG_SUPPRESSED:
                    logging.info(
                        f"SUPPRESSED [{channel}/{severity}] "
                        f"{title}: {clean_message}"
                    )
                self.last_outcome = "suppressed"
                return False
            # First time at this severity, or its cooldown has elapsed.
            # The cooldown starts on the attempt, so a failing toast backend
            # cannot cause a retry (and a log line) on every polling cycle.
            self._mark_sent(state["sent_at"], severity, now)
            return self._deliver(title, message, clean_message, counters)

        # First breach on this channel.
        self._state[channel] = {"severity": severity, "sent_at": {}, "ok_streak": 0}
        self._mark_sent(self._state[channel]["sent_at"], severity, now)
        return self._deliver(title, message, clean_message, counters)

    @staticmethod
    def _mark_sent(sent_at, severity, now):
        """Record a toast at `severity`; it also covers every lower severity."""
        for name, rank in SEVERITY_RANK.items():
            if 0 < rank <= SEVERITY_RANK[severity]:
                sent_at[name] = now

    # ------------------------------------------------------------------
    def report_ok(self, channel):
        """
        Call once per polling cycle when the channel has a VALID reading
        in the Good range. Do not call it for None/stale readings.
        Returns True on the cycle the channel's state is cleared.
        After `clear_after` consecutive calls the channel's alert state is
        cleared, so the next breach toasts immediately.
        """
        state = self._state.get(channel)
        if state is None:
            return False
        state["ok_streak"] += 1
        if state["ok_streak"] >= self.clear_after:
            del self._state[channel]
            self.stats[channel]["recovered"] += 1
            logging.info(
                f"RECOVERED [{channel}] after {self.clear_after} "
                f"consecutive normal readings"
            )
            return True
        return False

    # ------------------------------------------------------------------
    def _show_toast(self, title, message):
        from winotify import Notification, audio

        toast = Notification(app_id="Intelligent Hardware Sentinel",
                             title=title,
                             msg=message)
        toast.set_audio(audio.Default, loop=False)
        toast.show()

    def _deliver(self, title, message, clean_message, counters):
        full_message = f"{title}: {clean_message}"
        try:
            self._show_toast(title, message)
        except Exception as e:
            counters["failed"] += 1
            self.last_outcome = "failed"
            logging.warning(f"{full_message} (toast failed: {repr(e)})")
            return False
        counters["sent"] += 1
        self.last_outcome = "sent"
        logging.warning(full_message)
        return True
