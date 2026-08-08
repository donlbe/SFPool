#!/usr/bin/env python3
"""Generate and serve the SF public-pool lap-swim calendar feed."""

from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from threading import Thread
from time import sleep
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "outputs" / "sf-public-pools-lap-swim.ics"
WATCH_STATUS = ROOT / "outputs" / "schedule-watch-status.json"

WEEKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}


def ical_escape(value):
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def fmt_time(hhmm):
    return datetime.strptime(hhmm, "%H:%M").strftime("%-I:%M %p")


def first_occurrence(start_date, day):
    date = datetime.strptime(start_date, "%Y%m%d")
    return date + timedelta(days=(WEEKDAY[day] - date.weekday()) % 7)


def generate():
    config = json.loads((ROOT / "schedules.json").read_text())
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//SF Pool Lap Swim Feed//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH", f"X-WR-CALNAME:{ical_escape(config['calendar_name'])}",
        "X-WR-TIMEZONE:America/Los_Angeles",
        "BEGIN:VTIMEZONE", "TZID:America/Los_Angeles", "X-LIC-LOCATION:America/Los_Angeles",
        "BEGIN:DAYLIGHT", "TZOFFSETFROM:-0800", "TZOFFSETTO:-0700", "TZNAME:PDT",
        "DTSTART:19700308T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", "TZOFFSETFROM:-0700", "TZOFFSETTO:-0800", "TZNAME:PST",
        "DTSTART:19701101T020000", "RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU", "END:STANDARD",
        "END:VTIMEZONE",
    ]
    for index, session in enumerate(config["sessions"], 1):
        first = min(first_occurrence(session["start_date"], day) for day in session["days"])
        start = first.strftime("%Y%m%d") + "T" + session["start"].replace(":", "") + "00"
        end = first.strftime("%Y%m%d") + "T" + session["end"].replace(":", "") + "00"
        # RFC 5545 requires UNTIL to be UTC when DTSTART has a TZID.  The
        # previous feed emitted a floating UNTIL, which Google Calendar can
        # treat as an invalid/ended recurrence series.
        end_of_schedule = datetime.strptime(session["end_date"], "%Y%m%d") + timedelta(days=1, seconds=-1)
        until = end_of_schedule.replace(tzinfo=ZoneInfo(config["timezone"])).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        time_range = f"{fmt_time(session['start'])}-{fmt_time(session['end'])}"
        title = f"Lap Swim - {session['pool']} ({time_range})"
        description = f"Official SF Recreation & Park summer 2026 schedule. {time_range}."
        if session.get("note"):
            description += f" {session['note']}."
        lines += [
            "BEGIN:VEVENT", f"UID:sf-pool-lap-{index}@sf-pool-feed.local", f"DTSTAMP:{stamp}",
            f"DTSTART;TZID=America/Los_Angeles:{start}", f"DTEND;TZID=America/Los_Angeles:{end}",
            f"RRULE:FREQ=WEEKLY;BYDAY={','.join(session['days'])};UNTIL={until}",
            "EXDATE;TZID=America/Los_Angeles:" + ",".join(
                closure + "T" + session["start"].replace(":", "") + "00" for closure in config["citywide_closures"]
            ),
            f"SEQUENCE:{config.get('feed_revision', 1)}", f"SUMMARY:{ical_escape(title)}", f"LOCATION:{ical_escape(session['pool'] + ', San Francisco, CA')}",
            f"DESCRIPTION:{ical_escape(description)}", "STATUS:CONFIRMED", "TRANSP:OPAQUE", "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", newline="") as output:
        output.write("\r\n".join(lines) + "\r\n")
    return OUTPUT


def schedule_watch(force=False):
    """Look for new schedule PDFs near the loaded schedule's end date.

    New PDFs are reported rather than automatically parsed into calendar events:
    their timetable layouts require a source review before publishing a new feed.
    """
    config = json.loads((ROOT / "schedules.json").read_text())
    cycle_end = max(datetime.strptime(s["end_date"], "%Y%m%d").date() for s in config["sessions"])
    today = datetime.now().date()
    previous = json.loads(WATCH_STATUS.read_text()) if WATCH_STATUS.exists() else {}
    if not force and (today < cycle_end - timedelta(days=21) or previous.get("checked_on") == today.isoformat()):
        return previous or {"status": "not_due", "next_check_window": str(cycle_end - timedelta(days=21))}

    results = []
    for source in config["schedule_sources"]:
        result = {"pool": source["pool"], "page": source["page"], "expected_document_id": source["document_id"]}
        try:
            request = Request(source["page"], headers={"User-Agent": "SFPoolCalendar/1.0"})
            with urlopen(request, timeout=20) as response:
                page = response.read().decode("utf-8", errors="replace")
            ids = sorted(set(re.findall(r"DocumentCenter/View/(\\d+)", page)))
            result["found_document_ids"] = ids
            result["changed"] = source["document_id"] not in ids
            result["status"] = "new_schedule_available" if result["changed"] else "unchanged"
        except Exception as exc:
            result["status"] = "check_failed"
            result["error"] = str(exc)
        results.append(result)

    status = {
        "checked_on": today.isoformat(),
        "cycle_end": cycle_end.isoformat(),
        "review_required": any(item["status"] != "unchanged" for item in results),
        "results": results,
    }
    WATCH_STATUS.write_text(json.dumps(status, indent=2) + "\n")
    return status


def watch_forever():
    """Run one check per day while the service is online."""
    while True:
        schedule_watch()
        sleep(24 * 60 * 60)


class CalendarHandler(BaseHTTPRequestHandler):
    def send_calendar(self, include_body):
        if urlparse(self.path).path not in ("/", "/calendar.ics", "/calendar-v3.ics", "/sf-public-pools-lap-swim.ics"):
            self.send_error(404)
            return
        feed = generate()
        content = feed.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/calendar; charset=utf-8")
        self.send_header("Content-Disposition", 'inline; filename="sf-public-pools-lap-swim.ics"')
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "public, max-age=300")
        self.end_headers()
        if include_body:
            self.wfile.write(content)

    def do_GET(self):
        if urlparse(self.path).path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if urlparse(self.path).path == "/schedule-status.json":
            content = json.dumps(schedule_watch(), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        if urlparse(self.path).path == "/check-schedules":
            content = json.dumps(schedule_watch(force=True), indent=2).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        schedule_watch()
        self.send_calendar(True)

    def do_HEAD(self):
        self.send_calendar(False)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "generate":
        print(generate())
    else:
        port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8080"))
        print(f"Serving calendar feed at http://localhost:{port}/calendar.ics")
        Thread(target=watch_forever, daemon=True).start()
        ThreadingHTTPServer(("0.0.0.0", port), CalendarHandler).serve_forever()
