"""Pure due-time logic: when does a schedule fire, and did this minute already fire.

The semantics mirror the panel's static/schedule.js clock math (daily every day,
weekly by 0=Sunday..6, monthly by day of month) and both run on local wall-clock
time, so the panel's forecast and the actual firing agree.
"""
from datetime import date, datetime


def day_matches(schedule: dict, day: date) -> bool:
    """Whether the schedule's cadence fires on this date. Backend weekdays are
    0=Sunday..6; ``isoweekday() % 7`` maps python's Mon=1..Sun=7 onto that."""
    cadence = schedule.get("cadence", "daily")
    if cadence == "weekly":
        return (day.isoweekday() % 7) in (schedule.get("weekdays") or [])
    if cadence == "monthly":
        return day.day in (schedule.get("monthdays") or [])
    return True


def is_due(schedule: dict, now: datetime) -> bool:
    """Whether the schedule fires at this exact minute (enabled, day matches,
    and the current HH:MM is one of its times)."""
    if not schedule.get("enabled") or schedule.get("deleted"):
        return False
    if not day_matches(schedule, now.date()):
        return False
    return now.strftime("%H:%M") in (schedule.get("times") or [])


def due_run_id(slug: str, now: datetime) -> str:
    """The deterministic batch run id for one firing minute. Deriving it from
    slug + minute makes the firing idempotent end to end: the run strip carries
    it (so a restarted executor never double-fires) and the pipeline's own
    idempotency keys descend from it."""
    return f"{slug}-{now.strftime('%Y%m%d-%H%M')}"


def already_fired(schedule: dict, run_id: str) -> bool:
    """Whether the schedule's run strip already carries this firing."""
    return any(r.get("run_id") == run_id for r in schedule.get("runs") or [])


def latest_due(schedule: dict, now: datetime, window_hours: int = 24):
    """The schedule's most recent due minute strictly before `now`, within the
    window, or None. This is the startup catch-up's question: what should have
    fired while the executor was down."""
    from datetime import timedelta
    for minutes_back in range(1, window_hours * 60 + 1):
        candidate = now - timedelta(minutes=minutes_back)
        if is_due(schedule, candidate):
            return candidate
    return None
