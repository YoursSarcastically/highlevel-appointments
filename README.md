# Appointments — full-stack MVP

A HighLevel sub-account section for appointment businesses (salon, gym, studio), as a real application: SQLite database, JSON REST API, admin app and a public booking page. Standard-library Python only, so nothing to install.

## Run

```bash
python3 server.py            # http://localhost:8787
```

- Admin app: `http://localhost:8787/`
- Public booking pages: `http://localhost:8787/book/blushblade`, `/book/ironworksgym`, `/book/emberyogapilates`
- Database file: `appointments.db` next to `server.py` (override with `APPT_DB=/path/file.db`). Delete it to start over; the three demo businesses are re-seeded on the next start.

## Layout

| File | What it is |
|---|---|
| `server.py` | HTTP server, router, business rules (availability, conflicts, passes, payments), REST API |
| `db.py` | Schema (14 tables), connection, seed data for the three business types |
| `static/index.html` | Admin app — the prototype UI rewired to the API |
| `static/book.html` | Public booking page, served at `/book/<slug>` |
| `actions.js` · `onboarding.js` | Sources for the admin app's API layer and onboarding; `build_client.py` merges them into `index.html` |
| `hl.py` | HighLevel API v2 client (contacts, calendars, invoices, SMS, users, transactions) |
| `hl_tab.js` | The HighLevel tab in the admin app |
| `tests/test_e2e.py` | End-to-end suite (standard library `unittest`) |

## Data model

`locations` (one per business; status, onboarding, booking rules) · `staff` + `staff_hours` + `shifts` · `services` + `service_staff` · `classes` · `passes` + `client_passes` · `clients` · `appointments` · `sales` · `attendance` · `pages`. All child rows cascade on delete. Appointment status: `booked → arrived → done`, or `noshow`, or `cancelled`. Source: `online | walkin | google | phone`.

## API

All routes are JSON. Mutations return the full location state so the client can re-render from one source of truth. Errors are `{ "error": "..." }` with 400 / 404 / 409.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/locations` | List businesses |
| GET | `/api/locations/:loc/state` | Full snapshot for the admin app |
| PATCH | `/api/locations/:loc` | `status`, `onboarded`, `slot`, `cancelHours`, `deposit`, `features` (`{booking, passes, classes, tips, reminders, google}`) |
| GET | `/api/locations/:loc/availability?service_id&staff_id?&date` | Free slots |
| POST | `/api/locations/:loc/appointments` | Book (`service_id, staff_id?, client_id, date, time, source`) or walk-in (`walkin: true`) |
| POST | `/api/locations/:loc/appointments/:id/checkin` · `/unarrive` · `/noshow` · `/cancel` | Status changes |
| POST | `/api/locations/:loc/appointments/:id/pay` | `{ method: 'Tap to pay' \| 'Card on file' \| 'Cash' \| 'Pass', tip }` |
| POST · PATCH · DELETE | `/api/locations/:loc/staff[/:id]` | Staff CRUD |
| PUT | `/api/locations/:loc/staff/:id/hours` | `{ "<dow>": [open, close] \| true \| null }` |
| POST | `/api/locations/:loc/staff/:id/clock` | Toggle clock in / out |
| POST · PATCH · DELETE | `/api/locations/:loc/services[/:id]` | Services CRUD (`online`, `staff[]`) |
| POST · PATCH · DELETE | `/api/locations/:loc/classes[/:id]` | Classes CRUD |
| POST · DELETE | `/api/locations/:loc/classes/:id/attendance[/:client_id?date=]` | Roster check-in / remove |
| POST · DELETE | `/api/locations/:loc/passes[/:id]` | Passes |
| POST | `/api/locations/:loc/passes/:id/sell` | `{ client_id }` |
| POST | `/api/locations/:loc/clients` | `{ name, phone? }` |
| PATCH | `/api/locations/:loc/pages/:id` | `{ on }` |
| POST | `/api/locations/:loc/demo/rush` · `/demo/reset` | Demo controls |
| GET | `/api/public/:slug` | Public info: online services, staff, status |
| GET | `/api/public/:slug/availability?service_id&staff_id?&date` | Public slots (empty when Busy/Closed today) |
| POST | `/api/public/:slug/book` | `{ service_id, staff_id?, date, time, name, phone? }` |

## Onboarding and tools

Onboarding has four steps: business type → services (and classes for gym/studio, add and remove inline) → team (invite inline) → tools. Tool switches are stored per location in `locations.features` and gate the UI: passes off hides the passes section and "Sell a pass" everywhere, tips off removes the tip row from checkout, classes off hides rosters and class blocks, and booking off turns the public Book page off (`pages.book`).

## Tests

```bash
python3 tests/test_e2e.py
```

Boots the real server on a throwaway database and drives 34 end-to-end checks over HTTP: seed shape, onboarding, front-desk pay and pass redemption, walk-ins, availability and conflicts, team hours and clock, services and pages, the public booking page, classes and rosters, demo controls, tool switches, error handling, and persistence across a restart.

## HighLevel integration

`hl.py` is a thin client for the HighLevel API v2 (`services.leadconnectorhq.com`, `Version: 2021-07-28`, calendars `2021-04-15`). Credentials come from `hl.config.json` next to the server (owner-only permissions, git-ignored, excluded from the zip) or from `HL_TOKEN` / `HL_LOCATION_ID`. Set them up in the app's **HighLevel** tab: enter the sub-account id, test, link the business, then run **Sync team**.

| Local action | HighLevel call |
|---|---|
| New client (desk, walk-in, public page) | `POST /contacts/upsert` — tagged `appointments-app`, phone-matched |
| Booking | `POST /calendars/events/appointments` on the service's calendar (or the staff calendar), assigned to the staff member's user, in the sub-account's timezone |
| No-show · cancel · pay | `PUT /calendars/events/appointments/:id` → `noshow` · `cancelled` · `showed` |
| Pay (when Invoices is on) | `POST /invoices/` then `POST /invoices/:id/record-payment` (cash / card / other for passes) |
| Booking (when SMS is on) | `POST /conversations/messages` type `SMS` — real texts, real credits |
| Sync team | `GET /users/`, `GET /calendars/`, matches staff to users by name (a one-user sub-account links everyone to that user) |
| Services & booking → Push / Import | `POST /calendars/` one calendar per service (team = qualified staff), `GET /calendars/` to bring calendars in as services; each linked service shows its HighLevel booking widget link |
| Website & pages → Load / Use as website | `GET /funnels/funnel/list` lists funnels and websites with their pages; the chosen one becomes the business's website |
| Import / push contacts | `GET /contacts/` · `POST /contacts/upsert` |
| Payments panel | `GET /payments/transactions` |
| Products → Push | `POST /products/` + `POST /products/:id/price` — one product per service (one-time price) and per pass (monthly recurring for memberships); invoices then carry `productId`/`priceId` |
| Workflows | `GET /workflows/` to map; `POST /contacts/:id/workflow/:wid` enrols the contact after a booking, a no-show, a paid visit, a pass sale, and 7 days before a pass ends |
| Opportunities | `GET /opportunities/pipelines` to map; `POST /opportunities/` at a client's first booking, `PUT /opportunities/:id` to the no-show stage or to the paid stage as `won` |
| End of day → Compare | `GET /calendars/events`, `GET /payments/transactions`, `GET /invoices/` next to the desk's own numbers (read-only) |
| Forms & surveys | `GET /forms/`, `GET /surveys/`, `GET /forms/submissions`, `GET /surveys/submissions` shown on the client drawer; the intake form gets a widget link |
| Email | `POST /conversations/messages` type `Email`: receipt after payment, pass-expiry heads-up (hourly check), and a one-click lapsed-clients email |
| Custom objects | `POST /objects/`, `POST /custom-fields/folder`, `POST /custom-fields/`, `POST /associations/` once (Set up); then `POST /objects/:key/records` + `POST /associations/relations` for every pass sold, class visit and shift |

Sync switches (`contacts`, `appointments`, `workflows`, `opportunities`, `invoices`, `email`, `sms`, `objects`) and the workflow / pipeline / intake-form mappings live in the config. Defaults: contacts, appointments, workflows and opportunities on; invoices, email, SMS and custom objects off. Only the business you **Link** syncs; the other demo businesses stay local, and the demo rush never writes to HighLevel. Every call is logged in `hl_log` (shown in the tab) and a failure never blocks the front desk. Tests run with an empty config so they cannot reach a real account.

## Rules enforced server-side

- A slot exists only if the staff member works that weekday, the service ends before their close, no appointment or class overlaps, and (today) it starts at least 30 minutes from now.
- Booking re-checks the slot; a taken slot returns 409. "Anyone" picks the first qualified free person.
- Paying with a pass requires a valid, matching pass; packs lose one credit, memberships do not. Every payment increments the client's visits and writes a sale attributed to the staff member.
- Class check-in refuses when full, uses a credit when the client holds one, and records a drop-in sale otherwise.
- Clock-in is scoped to the day; clock-out writes a shift row.

## What is not here yet

Authentication and HighLevel OAuth (the integration uses a private integration token), automated reminders, deposits, Google Reserve. The data mapping for those is in the PRD (section 8).
