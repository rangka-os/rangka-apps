# Work shifts (module shifts, rangka-apps#120)

Planned working time of outlet staff. A cash drawer session (`pos_shift`, module pos) is a different thing: it may link
to the work shift but never needs one, and a cash session never requires a clock-in.

## Models
- `shift_template`: a named pattern per outlet (start, end; an end before the start ends the next day).
- `shift_roster` + `shift_roster_line`: a weekly grid document per outlet. Submitting it (flow `shift_roster_publish`)
  writes one published `shift_assignment` per employee per day (unique by `day_key`); cancelling unpublishes.
- `shift_assignment`: the employee's shift or day off. Staff read only their own published days (`if:` on the member
  grant). `start_at` and `end_at` are computed with the outlet's time zone offset.
- `shift_swap`: a same-day swap request, approved by the supervisor of the outlet (flows `shift_swap_apply`, `shift_swap_revert`).

## Clock screen
`absen_outlet` (module attendance, which requires shifts) has a `clock.shift` slot (rangka-os contract 0030):
- The screen lists `shift_assignment` with the filter `employee == user.name && published && !day_off && work_date == today()`,
  takes the earliest start and shows "Shift Malam 22.00 sampai 06.00", the outlet, and the minutes late or early.
- At clock-in the log's `work_shift` is filled; flow `shift_attendance_link` does the same on the server, so a client
  that ignores the slot still links.
- Lateness is measured by the record, not the phone: `staff_attendance.sched_start` is the linked shift's start, else the
  outlet's default hours (`work_start`); no shift today means the outlet hours; neither means no lateness is measured.
  The outlet grace (`late_grace_min`) applies to both: 22:20 against a 22:00 shift with 10 minutes grace is 10 late.

## Dashboards and menu
Dashboards `jadwal_saya` (own shifts, next 14 days, open swaps) and `jadwal_outlet` (head office and supervisors) are
in the bundle `absen_karyawan`, which carries the clock work entry and the menu group "Jadwal shift". `fnb_cabang`,
`ritel_cabang` and `kasir` install module shifts; `karyawan_gaji` lists it for the schedule on the `saya` dashboard.
