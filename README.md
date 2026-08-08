# SF public-pool lap-swim calendar

Run `python3 app.py` and subscribe to `http://localhost:8080/calendar-v3.ics`.

The source data is in `schedules.json`; regenerate the downloadable calendar with
`python3 app.py generate`. The feed contains only sessions labelled "Lap Swim" in
the official Summer 2026 schedules for Balboa, Mission, Sava, Rossi, Garfield,
and Hamilton pools. It excludes citywide closures on June 19 and July 4.

The feed uses RFC 5545-compliant UTC `UNTIL` values for recurring events. This
is important for Google Calendar to display occurrences after the initial event.

During the final 21 days of the loaded schedule, the running service checks each
official pool page once per day for a new schedule PDF. Check the report at
`/schedule-status.json`, or run a check immediately at `/check-schedules`.
When a new PDF appears, review its timetable and update `schedules.json`; this
deliberately prevents an unreviewed PDF layout change from publishing bad times.

## Deploy for Google Calendar subscription

Google Calendar can subscribe only to a stable, publicly reachable HTTPS URL.
This repository includes a `Dockerfile` and `render.yaml` for deployment as a
Render web service:

1. Put this folder in a private or public GitHub repository.
2. In Render, choose **New > Blueprint**, connect that repository, and approve
   the `render.yaml` service. Select a paid instance that stays online; the
   schedule watcher only runs while the web service is running.
3. After deployment, copy `https://<your-service>.onrender.com/calendar.ics`.
4. In Google Calendar on the web: **Other calendars > From URL**, paste that
   HTTPS link, then choose **Add calendar**.

The deployment listens on Render's `PORT` environment variable and has a
`/healthz` endpoint for its health check. The public feed is intentionally
read-only; do not put editing credentials in the feed URL.
