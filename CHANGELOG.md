# Changelog

## 1.1.0

A hardening and completeness release. Every fix below was reproduced against 1.0.0 first and now has a regression test.

### Fixed
- **A single unparseable timestamp permanently broke a case.** An app-usage CSV with an offset timestamp was stored with a corrupt UTC value, the session rebuild then raised, the API returned an error, and every later upload to that case failed. Timestamps are now parsed strictly, bad rows are skipped and counted, and ingest is a single transaction.
- **The case timezone was decorative.** Naive local times were treated as UTC and `tz_assumed` still said UTC. The case timezone is now applied, the basis of every timestamp (`absolute` / `offset` / `assumed`) is recorded, and a `TZ_ASSUMED` finding is raised.
- **The timeline silently truncated at 5,000 events** (a 7,000-event case lost its whole tail). The API now pages and reports `total` / `has_more`; the UI shows density when zoomed out and never hides the truncation.
- **Rejected uploads left orphan evidence files on disk.** Files are parsed before anything is stored.
- **Files were classified by name** (`whatsapp_location.csv` became "app usage"). Detection is content-based.
- **Session IDs changed on every rebuild** and correlation was O(n²) (20,000 events: 4.9 s). The algorithm is now a deterministic single sweep (about 0.1 s).
- README documented API paths that returned 404; Chromium timestamp handling guessed at units; the Plaso JSON-lines parser did not understand modern `json_line` output.

### Security
- **Stored XSS in the HTML report**: Jinja autoescape was silently off for `report.html.j2`. Escaping is now unconditional and the report is served with a no-script CSP.
- Wildcard CORS with credentials and no `Host` check let any web page the examiner visited read or delete cases. Added a `Host` allow-list and an `Origin` check on writes.
- CSV formula injection, evidence URLs rendered as live links, XML entity bombs (`defusedxml`), unquoted SQLite identifiers from hostile databases, and bidi-override spoofing in titles.
- Evidence copies are read-only; uploads no longer block the event loop.

### Added
- Formats: Firefox / Fenix history, GPX, Google Takeout `Records.json`, modern Plaso JSON lines; Chromium search terms, downloads, and decoded transitions; decoded Android usage event types.
- Chain of custody: hash-chained audit log, integrity verification, report section.
- Zoom / pan timeline with overview brush and density view, offline location track, session focus, timezone toggle, correlation settings, dark theme, keyboard access, multi-file upload with progress.
- Case editing, validation of timezone names, new validation findings (`ROWS_SKIPPED`, `TZ_ASSUMED`, `NO_TIME_OVERLAP`, `NO_SESSIONS`, `EARLY_TS`).
- Endpoints: `PATCH /cases/{id}`, `/verify`, `/audit`, `/overview`, `/locations`, `/events/{id}`, `/sessions/{id}/events`, `/sessions/params`, `/api/meta`.
- Docker image and compose file, single-process run scripts, GitHub Actions CI.
- Tests: from 15 backend tests to a full unit + API integration suite, plus frontend unit and component tests.

### Changed
- Fonts are bundled (the UI no longer contacts Google Fonts); TypeScript `strict` is on; `requirements.txt` no longer includes test tools (see `requirements-dev.txt`).
- Node 20.19+ / 22.12+ is required (Vite 8); the README previously said 18+.
