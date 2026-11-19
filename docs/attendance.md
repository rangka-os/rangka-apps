# Absen karyawan (module `attendance`, piece `absen_karyawan`)

Outlet staff (cashier, outlet staff, kitchen, warehouse and office staff) clock in and out from their own phone at an
outlet, with the phone's position and a selfie. Issue rangka-apps#100 (plan `P0-attendance.md`). Kernel contracts 0030
(clock screen, `geo`, `image`, `distance_m`), 0007 (approvals), 0010 (row scope), 0011 (attachments), 0025 (flow read
locks), 0056 (employee records). Project field workers keep their own clock (module `projects`, `attendance_log`):
project billing and work sites differ, so the two are not merged.

## Setup per outlet (fields added to `outlet`, shown only when `attendance` is installed)
| Field | Meaning |
|---|---|
| `lat`, `lng` (`point`) | The outlet's point. Without one every clock-in counts as "no position": held, never refused |
| `radius_m` | Geofence in metres, default 100, 20 to 2000. Indoor GPS in malls drifts 50 to 150 m: widen per outlet |
| `time_zone` | WIB, WITA or WIT (the org timezone is not readable in CEL, so lateness uses the outlet's own zone) |
| `work_start`, `work_end` | Default hours `HH:MM`. Empty = no lateness or early leave measured. `work_end` before `work_start` ends the next day |
| `late_grace_min` | Grace in minutes before a clock-in counts as late (default 10) |

`user.attendance_exempt` (Tidak wajib absen): the owner marks people who are not expected to clock in (owner, office
manager); they get no monthly summary and are left out of the morning digest.

## What is measured where (`staff_attendance`, Absen karyawan)
- One log per employee per day (flows `staff_attendance_guard_*` under the 0025 flow lock). A draft without a clock-in
  time does not hold the day.
- `employee` defaults to the member's own employee record (`user.name`); `outlet` to their first outlet. Staff may only
  name themselves (rule `employee_self`) and their own outlets (rule `own_outlet`).
- Clock times, positions and photos are `set_once`. A wrong log is cancelled by head office and replaced by a manual log
  (`source: manual`, no selfie needed, always "no position").
- `in_inside`/`out_inside`: `distance_m(position, outlet point) <= radius`; `outside_site` when either is not.
- `late_min` = whole minutes after `sched_start` less the outlet's grace (08:25 against 08:00 with 10 minutes grace is
  15); `early_min` = minutes before `sched_end`; `worked_min` = clock-out minus clock-in. A UTC and a local clock-in
  string give the same result (`epoch`). The schedule is the outlet's default hours; shifts (rangka-apps#120) will
  fill it per employee.
- The outlet's point, radius, zone and hours are frozen with the log at submit: moving an outlet later leaves old logs
  measured where they were taken.

## Approvals (contract 0007), approvers `outlet_supervisor` and `head_office`
| Policy | When |
|---|---|
| `outside_site` | outside the radius, or no position (reason of 5+ characters required at clock-in) |
| `late_entry` | the log is submitted more than one day after its date |
| `overtime` | `overtime_hours > 0` (reason required) |

Lateness alone is **not** held: it is a fact for the summary and, later, for payroll rules (rangka-apps#101). Head office
is an approver, so its own manual corrections count at once (still marked outside, with the reason).

## Monthly summary (`attendance_period`, `attendance_summary`)
Head office submits an `attendance_period` (at most 31 days, after its last day) for one outlet or the whole business.
Flow `attendance_period_on_submit` writes one `attendance_summary` per active, non-exempt employee (a one-outlet period:
only those who clocked in there): days present, late count and minutes, early leave minutes, overtime hours, minutes
worked. Logs waiting for approval do not count. While the period is submitted, logs dated inside cannot be submitted or
cancelled; an overlapping period of the same outlet (or the whole business) is refused. Cancelling the period marks its
summaries `cancelled`. Leave and absent days arrive with leave (#114) and shifts (#120).

## Screen, dashboards, notice
- Screen `absen_outlet` (type `clock`, phone, location and photo): the member's open log; the outlets offered are their
  own (head office without outlets: every active outlet). Submit at clock-out. No offline queue (the selfie needs the
  connection).
- Piece `absen_karyawan`: dashboards `absen_hari_ini` (head office, outlet supervisor: present, late, outside, waiting),
  `absen_karyawan_saya` (staff: own days and late minutes), `rekap_absen_karyawan` (summaries). The `employee` role lands
  on the clock; other staff open it from Kerja.
- Flow `attendance_missing_digest` (10:00 daily): notice `attendance_missing` to head office and the owner listing
  active staff with an outlet and no clock-in today. Without shifts a day off shows too. Scheduled flows run for the
  default org only until rangka-os#263 slice a.

## Privacy (UU PDP)
Positions and selfies are personal data. Staff read only their own logs and summaries (permission `if: employee ==
user.name`, which also filters lists and dashboards, so colleagues at the same outlet do not see each other's selfies).
The outlet supervisor reads the logs and summaries of their outlets (row scope `outlet`); head office reads all. A selfie
is an attachment of its log, readable only by whoever can read the log.

## Not in v1
Kiosk clock-in on a shared outlet tablet (contract 0048), face match against a reference photo (phase 2, paid AI
credits), fingerprint machine import, fake GPS detection, breaks. Kitchen staff (role `kitchen`, module `kitchen`) are
not granted by this module, so it installs without `kitchen`: give them the `employee` role to clock in.
