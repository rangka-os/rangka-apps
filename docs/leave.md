# Leave and permits (module `leave`, rangka-apps#114)

Staff ask for annual leave, sick days or an unpaid permit from the phone; the outlet supervisor (or head office) approves;
the annual balance moves by itself; unpaid leave reaches the payslip. Apps: `karyawan_gaji` 1.3 installs it (menu
"Cuti dan izin", screen `ajukan_cuti`, widgets on the `Saya` page).

## Models

| Model | What it is |
| --- | --- |
| `leave_type` | Annual leave (12 days, the only one with a balance), sick with a doctor's note, unpaid permit, maternity (90 calendar days), cuti bersama. Five rows are made when a site has none (flow `leave_grant_daily`; `demo-karyawan` seeds the same). Fields: `kind` paid or unpaid, `tracked`, `annual_days`, `calendar_days` (weekends count), `max_days`, `needs_note`, `joint_deducts` (the per-site cuti bersama setting). |
| `leave_profile` | Join date and outlet per employee (the join date is not on the employee record). Head office fills it once; `opening_used` carries days already taken when a business moves from paper. |
| `leave_balance` | One per employee, tracked type and year: `granted`, `used`, `joint_used`, `remaining`. Written only by flows. |
| `public_holiday` | National holidays and cuti bersama (`joint`). One row per date. |
| `leave_request` | The request. Draft = requested (made by the employee, who never submits it); submit = approved; cancel = cancelled; `rejected` + `decision_note` = refused. `days`, `holiday_days`, `balance_left`, `status`. |
| `leave_guard` | Never stored; the flows' refusals (`over_balance`, `no_balance`, `days_stale`, `overlap`, `period_closed`). |

## Rules

- **Days.** Monday to Friday between the two dates, minus public holidays on a weekday; a type with `calendar_days`
  counts every day. The weekday count is a closed form (whole weeks times 5 plus the weekdays of the rest, from Monday
  3 Jan 2000), the holidays are counted by flow `leave_request_prepare_*` when the draft is saved and checked again at
  approval (a holiday added since makes the request `days_stale`: save it again). Half days are out of scope.
- **Approver.** The outlet supervisor of the request's outlet (row scope `outlet`), else head office. A supervisor never
  approves their own request. The submit is the approval (contract 0007). Cancelling approved leave is by the approver or
  head office, never the employee.
- **Annual grant** (flow `leave_grant_daily`, every morning 06:00 or by hand with `event.as_of`): 12 days for the year,
  once, on the later of 1 January and the first work anniversary (UU Ketenagakerjaan: the right starts after 12 months of
  continuous work). Monthly accrual is out of scope. An employee with less than 12 months has no balance: annual leave is
  refused with "Belum ada jatah cuti".
- **Cuti bersama** takes one day off every annual balance of the year (`joint_used`), once, when the day is added (flow
  `public_holiday_joint_apply`); a balance granted later counts the joint days it covers. Untick `joint_deducts` on the
  annual type to leave balances alone. A joint day added by mistake is not undone by a flow: no screen edits the balance numbers, so it is
  fixed by the platform owner (open item).
- **Balance under lock.** The approval and the cancel read `leave_balance` before writing (contract 0025 flow read lock), so
  two approvals sent together run one after the other and the second sees the first one's days (day_one scenario
  `leave_concurrent_requests_cannot_overdraw`). The save of a draft also refuses days over the balance, for an early answer.
- **Doctor's note.** `attach` is a regular attachment field on the request form (no kernel `capture`); a type with
  `needs_note` refuses the approval without it.
- **Leave is decided before the attendance period.** Approving or cancelling leave whose days a submitted attendance
  period covers is refused (`period_closed`): payroll reads unpaid days from that period's summaries, so a later change would
  never reach the slip. Cancel the period first.

## Bridge to attendance and payroll

- `attendance_summary` gets `leave_days` (paid) and `unpaid_leave_days`, written by flow `attendance_period_on_submit`:
  each approved request is cut to the period, weekdays (or all days) less public holidays. A person with leave and no
  clock-in still gets a summary in an outlet period. A day on leave is not reported missing by `attendance_missing_digest`.
- The slip flows (`payroll_run_fill`, `payroll_run_fill_on_create`, `payslip_from_attendance`, written by
  `scripts/gen_payroll_fill_flows.py`) copy `unpaid_leave_days` into `payslip.unpaid_days`. A daily worker is also paid for the
  days of paid leave (`days` = present + paid leave days); a monthly salary is paid in full.
- **`unpaid_deduction`** = monthly salary / 25 x unpaid days (21 in a five-day week) for a monthly salary, taken off
  `base_pay`; daily wage x unpaid days for a daily worker. A daily worker is paid for days present only, so their unpaid days
  are already outside `days`: the amount is printed as "sudah tidak dibayar" and never taken off `base_pay` a second time. The
  basis is stated on the slip (`unpaid_basis`, printed). Amounts come from records only: the leave requests and the profile.
- This replaces the lines of `docs/payroll.md` that say unpaid days are not filled.

## Out of scope

Monthly accrual, half days, carry-over of unused days, a calendar import of the government's SKB list, leave across a
year boundary for annual leave (split it), and notifications to the approver.
