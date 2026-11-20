# Payroll (module `payroll`)

Monthly pay with BPJS and PPh 21 and the deposits of the month, in two kinds of payroll run (2.0):
- **client** (Kontrak klien): outsourced workers (manpower, pilot A) of one client contract, from the attendance recap,
  with the margin of the contract and the client invoice. Installed with `jasa_proyek`; demo site `demo-manpower`.
- **internal** (Karyawan sendiri): the firm's own staff, no project and no client invoice, optionally one outlet. Needs
  only `users` and `workforce`; `projects`, `invoicing`, `contacts`, `catalogue`, `outlets` and `attendance` are optional.

Tests: `tests/day_one_payroll.py` against the hand-written oracle `tests/payroll_oracle.py`; upgrade from 1.x
`tests/upgrade.py --site demo-manpower --check payroll_internal`.

## Flow of one month
1. Finance keeps one **Data gaji pekerja** (`payroll_profile`) per worker: NIK/NPWP, PTKP status (TER category A/B/C),
   pay basis (daily: pay per day x days present; monthly: fixed salary), fixed allowance, allowance per day present,
   BPJS programmes.
2. Finance opens a **Periode gaji** (`payroll_run`) per client contract (project) and cut-off period: tax year and
   month (the month paid), UMK of the site, JKK rate, JP and BPJS Kesehatan wage caps, how the client is billed.
3. The project admin imports and submits the attendance recap (`attendance_recap`, rangka-apps#28), or makes it from the
   workers' daily clock-ins: a submitted **Rekap absen lapangan** (`attendance_close`) turns the submitted daily logs
   (`attendance_log`, one per worker per day, checked against the work site's radius and approved by the site supervisor
   when outside it, late or with overtime) into the same draft recaps, which the project admin submits. Each submitted
   recap of a worker with a profile becomes a draft **Slip gaji** (`payslip`) of the open run of that project.
4. Finance adjusts drafts (overtime days for PP 35, THR months, other income, deductions) and submits the slips; the
   run's totals follow every submit and cancel.
5. Finance submits a **Tutup periode gaji** (`payroll_close`): the run is closed, the employer cost the recaps did not
   book goes on the project ledger (kind `payroll`), and the client invoice is made as a draft.

### Internal runs (payroll 2.0, rangka-apps#101)
- `run_kind` is `client` or `internal`. Runs made before 2.0 are all `client` (the upgrade fills the default); a new run
  defaults to `client` where `projects` is installed, else `internal`. A client run needs a project, an internal run has
  none and bills nobody (`billing_mode` none). Neither can change kind once a slip is submitted (the project is locked).
- `run_key`: client `project:period_from` (unchanged), internal `internal:<outlet>:period_from`. Overlapping runs are
  refused per project (client) or per outlet, empty = every outlet (internal); a worker paid twice for the same days is
  refused per slip either way.
- Slips are made by hand or from the staff attendance summaries (below). Closing an internal run only closes it:
  no project ledger row, no invoice. With `accounting` installed the close is journalled like any close (employer
  cost to 5-1300 against wages payable, PPh 21, BPJS and kasbon).
- The reminder reads no project for an internal run: it is ready when the period is over, it has a slip and no staff
  attendance period overlapping it (of its outlet or the whole business) is still a draft.
- Onboarding by file: `worker_list` takes an optional `Email karyawan` column (links the employee record in Tim), and
  `payroll_profile_list` imports the pay data per employee number (Status PTKP as `TK/0`..`K/3`, Dasar gaji
  Bulanan/Harian, BPJS Ya/Tidak).

### Slips from staff attendance (payroll 2.1, app karyawan_gaji)
- Finance saves an internal run with **Isi slip dari absen** (`fill_from_attendance`) ticked, at create or later: flows
  `payroll_run_fill_on_create` / `payroll_run_fill` make one draft slip (`from_fill`) per active worker with pay data
  whose employee record (`worker.employee`) has a non-cancelled `attendance_summary` inside the run's period, of the
  run's outlet when it names one. A daily worker needs at least one day present (a 0-day slip would leave the
  employee's BPJS share as a negative net pay, which is refused). A run of every outlet also gets a slip for each
  monthly worker without a summary (an owner or manager who does not clock in), except where the `projects` module is
  installed (monthly workers there may be outsourced staff paid by client runs). The tick is cleared and `fill_note`
  says what was done.
- A slip made by a fill stores what the summaries said (`att_days`, `att_late`, `att_ot`). A later fill refreshes such
  a draft when the worker's current summaries no longer add up to that snapshot or its link is not one of them (filled
  before the attendance was submitted, a period cancelled or submitted again). Finance may still edit days, lateness or
  overtime on the draft; a draft typed by hand is never rewritten. At submit `payslip_attendance_guard` refuses a filled
  slip whose snapshot no longer matches the current summaries (`attendance_changed`), and a slip linking a cancelled
  summary is refused by `attendance_still_valid`.
- Every morning at 07:00 (before the reminder at 07:15) flow `payslip_from_attendance` runs the same fill as the system
  for each open internal run whose period is over: new slips once (`auto_filled`), on the first morning no draft staff
  attendance period overlaps the run and the submitted summaries reach the period's first and last day (with the
  attendance module); on every morning after, drafts whose summaries changed are refreshed (attendance of another outlet
  submitted late reaches them). Drafts deleted or slips cancelled are not made again; ticking again does.
- From the summaries (summed when the period holds several): `days` = days present, `late_min_total`, `overtime_hours`,
  and the link `attendance_summary` (the last one). Not filled: `unpaid_days` (an absence is not unpaid leave until leave
  is recorded, rangka-apps#114: finance types it) and `overtime_days` (a summary has no count of days with overtime, so
  without a weighted overtime rate every hour counts 2x, never less than PP 35/2021 asks, until finance enters the
  days).
- One slip per worker per tax month: a worker who already has a live slip in the tax month (in this run or another) gets
  no new one. With one run per outlet, days a worker spent at a second outlet are therefore not paid by the second run;
  `fill_note` counts these workers, and summaries straddling the period's start or end (not read). Use one run of every
  outlet when staff move between outlets.
- A flow's writes start no flows, so the year to date and the open kasbon are read by the fill itself, as
  `payslip_from_recap` does. All three flows are written by `scripts/gen_payroll_fill_flows.py`.
- Finance reads the summaries (`attendance_summary` grants `finance_manager` read in attendance 1.0.2), never the logs
  with position and selfie. Which user's attendance pays which worker follows `worker.employee`, which head office may
  also edit (workforce grants, unchanged).
- A slip already submitted keeps its pay when its attendance period is cancelled later (the summaries are marked
  cancelled, the link stays as the trail); cancel and amend the slip to correct it.

### Salary transfer list (`bank_transfer_csv`)
- Each slip carries `bank_name`, `bank_account`, `account_holder` (the worker's name), `pay_date` and `transfer_note`
  (`Gaji YYYY-MM`) from the pay data and the run, frozen at submit. A slip without an account number is left out of the
  list and counted on dashboard `gaji_ringkas` (no submit warning: cash pay and client runs stay as they are).
- The file is the payslip list export (rangka-os contract 0024), one row per submitted slip with an account:
  `GET /api/v1/export/r/payslip?filter=payroll_run == '<run>' && docstatus == 1 && bank_account != '' && net_pay > 0&columns=bank_account,account_holder,bank_name,net_pay,transfer_note&sort=account_holder`,
  CSV (add `sep=semicolon` for an Indonesian Excel) or `format=xlsx` (account numbers stay text, leading zeros kept).
  Needs the `export` grant on payslip (finance). Generic columns for a bank's bulk transfer upload; no bank API and no
  bank-specific layout. Dashboard `transfer_gaji` shows the same rows by pay date; `gaji_ringkas` counts slips without
  an account.

Every morning flow `payroll_reminder` tells finance and the owner which open run is ready to process (once per run) and
which pay date is three days away or less, with what is still missing. It runs nothing: see [automation.md](automation.md).

## Rules (sources: reports/keputusan-riset/payroll-manpower.md in the project folder)
- PPh 21 (permanent workers and anyone paid monthly): TER monthly (PP 58/2023, tables in `payslip.ter_bp`) on gross = cash pay + JKK + JKM + BPJS Kesehatan 4%;
  120% without a tax number; truncated to the rupiah. December (or `last_month`): Pasal 17 on the year (biaya jabatan
  5%, at most 500.000 per month; JHT 2% + JP 1% of the worker; PTKP; PKP rounded down to thousands) minus the earlier
  slips of the year in closed runs; negative = refund on the slip.
- Pegawai tidak tetap paid per day or per week (profile `employment` = casual, daily pay basis; PMK 168/2023, DJP
  leaflet "Simplifikasi penghitungan PPh Pasal 21"): bruto sehari (the slip's gross / days worked, present + rest days
  worked) x TER harian, 0% up to 450.000 and 0,5% up to 2.500.000; above 2.500.000 a day, the Pasal 17 layers on 50% of
  the gross paid (Pasal 16 ayat 2 huruf c, per payment, not cumulative). These slips are not in the year to date, so
  December recomputes the permanent months only. A permanent worker who turns casual leaves that employment: their
  last permanent slip is the recomputed one (last_month), else the first casual slip is refused. Paid monthly, the
  same worker takes the monthly TER (keep `permanent`). Permanent again later in the year starts a new period of work:
  the year to date counts only the slips after the last `last_month` slip that casual months followed (a `last_month`
  ticked by mistake while the worker stayed permanent does not cut the year).
- Kasbon (`cash_advance`, finance): Dr Piutang karyawan / Cr Kas or Bank. The slip that repays it names it
  (`cash_advance`, with `other_deduction`): the live slips never deduct more than the kasbon, and it cannot be cancelled
  while a submitted slip repays it. Two such slips submitted at the same moment run one after the other (flow read
  locks, contract 0025). A slip made while the worker has a kasbon not yet repaid notes it (`open_advance`); a
  deduction that does not link it is a warning at submit. It is read when the slip is made: a kasbon given after the
  draft (or a draft from before the upgrade) is not noted; make the slip again to check.
- Kasbon request and installments (payroll 2.2, rangka-apps#117): a staff member drafts their own kasbon (screen
  `ajukan_kasbon`: amount, monthly installment, purpose); the draft is the request. Finance picks the worker (staff do not
  read the worker list; a request names only the requester's own worker) and submits it, which pays the money out: the
  submit is the approval. A site that wants a second person above an amount adds an `approvals:` policy in
  `custom/cash_advance.rk` (contract 0007 §1). A kasbon is interest-free: the worker repays exactly the amount, so it
  stays a receivable (1-1330), never a loan product. One kasbon of a worker is open at a time (`cash_advance_open_guard`,
  read from the kasbon and the slips, so a kasbon of before 2.2 counts), so a slip repays one kasbon.
  `cash_advance_balance` keeps what is left (a submitted kasbon cannot change): made at submit, `repaid_total`
  recomputed from the submitted slips that link the kasbon on every slip submit and cancel, `state` open, repaid or
  cancelled. Every slip-making flow (`payslip_from_recap`, the three attendance fills, `payslip_ytd_on_create` for a slip
  made by hand) puts `cash_advance` = the open kasbon and `other_deduction` = min(installment, remaining) when the kasbon
  has an installment; finance may change both while the slip is a draft, and the cap of `payslip_advance_guard` stays.
  A kasbon without an installment (0, all kasbon of before 2.2) keeps the typed deduction.
- Expense claims paid with the salary (module `reimbursement`, docs/reimbursement.md): `reimbursement` is the sum of the
  employee's approved claims to be paid with salary and still due, filled by the same slip-making flows. It is a
  company expense paid back against its receipt, not a reward for work, so not PPh 21 income: it is
  outside `cash_gross` and `gross_taxable`, so PPh 21 and BPJS do not change, and `net_pay = cash_gross - employee_bpjs
  - pph21 - other_deduction + reimbursement`. `total_deduction` (employee BPJS + PPh 21 + other deductions) is what the
  printed slip shows as Jumlah potongan. At submit the field must equal the claims still due
  (`claim_settlement_on_payslip_submit`, a claim approved, paid in cash or cancelled since refuses the submit until the
  field is corrected); they are then marked paid by the slip, and a slip cancel releases them. The run keeps
  `total_reimbursement`; the payroll close credits Utang upah with the whole net pay and debits 2-1510 Utang reimburse
  karyawan with the reimbursements, which the approved claims credited.
- BPJS wage = basic pay + fixed allowance, never below the UMK. JKK 0,24-1,74%, JKM 0,3%, JHT 3,7% + 2%, JP 2% + 1%
  up to the JP cap, BPJS Kesehatan 4% + 1% up to 12.000.000. Rounded half up to the rupiah.
- Overtime: the weighted pay rate per hour when set (worker or assignment), else PP 35/2021 on working days
  (1/173 of the monthly wage, first hour of each overtime day 1,5x, the rest 2x).
- Rest day or public holiday (PP 35/2021 Pasal 31), always at 1/173 of the monthly wage, entered as the days and the
  hours per multiplier summed over them: 6-day week, hours 1-7 of each day 2x, the 8th 3x, the 9th-11th 4x; 5-day week,
  1-8 2x, the 9th 3x, the 10th-12th 4x; a holiday on the shortest day of a 6-day week, 1-5 2x, the 6th 3x, the 7th-9th 4x.
  The tiers are checked in order: 3x hours need at least 5 (8 in a 5-day week) hours at 2x each, 4x at most 3 per 3x hour.
- THR: months of service, 12 or more = one month's wage, fewer pro rata.
- Invoice: wages on their own line outside the PPh 23 base (PMK 141/2015), employer BPJS and fee inside it; PPN on the
  whole invoice (the invoice has no per-line PPN; fee-only PPN under PMK 83/2012 is a follow-up). Fee: percent of
  the employer cost or flat per worker; or fee only.

## Guards (audit 7 Nov)
- A flow's writes start no flows (kernel), so `payslip_from_recap` itself refuses a second slip and fills the year to
  date; `payslip_month_guard_*` and `payslip_ytd_on_create` cover slips made by hand.
- One live slip per worker per tax month; a cancelled slip gives the month back.
- Year to date = every submitted slip of the worker in earlier tax months of the year. A slip whose year to date no
  longer matches is refused at submit (`payslip_ytd_guard`): delete the draft and make it again. Slips are submitted
  and cancelled in month order (`payslip_order_guard_*`), so a December recomputation never misses a month.
- Runs of one project never overlap (`payroll_run_overlap_guard_*`); a worker is never paid twice for the same days
  on two runs of any projects (`payslip_pay_overlap_guard`); the tax month is the month the period ends or the next.
- The slip guards check the worker, not the profile (a profile's worker can be changed).
- An invoice carrying a run's tag is submitted only while the run is closed by the close it names, and once
  (`invoice_payroll_once`): an amended copy of a cancelled invoice cannot bill the run again.
- A recap used by a submitted slip cannot be cancelled (`recap_cancel_guard`); `timesheet_cost` is computed from it.
- A run with submitted slips keeps its project, period, tax month and rates (`locked_by_slips`, copies on the first
  slip); a closed run keeps what its close froze (`locked_by_close`); runs are never deleted; one run per project and
  start of period.
- Fee only: the margin row takes the recaps' labour cost back off the project (the client funds the wages).
- Unpaid days (`unpaid_days`, monthly pay only): each costs salary / 25 (6-day week) or / 21 (5-day week), rounded to
  the rupiah, taken off the base pay, so PPh 21 is withheld on what is paid. The BPJS base stays the contractual wage
  (salary + fixed allowance, never below the UMK: upah pokok + tunjangan tetap of PP 44/2015 and Perpres 82/2018), and
  the overtime hour and THR keep the full month's wage. A daily worker is paid for days present, so the field stays 0.
- Lateness (`late_min_total` x the run's `late_rate_per_min`, off by default at 0): a pay cut off the cash gross, so PPh
  21 is withheld on less; the BPJS base stays the contractual wage. The rate is locked with the other run rates once a
  slip is submitted. A slip whose lateness takes more than half the base pay is warned at submit (PP 36/2021 Pasal 65).
- `payroll_profile_list`: Dasar gaji must be Bulanan/Harian (or Monthly/Daily), anything else is a row error; a row
  without NIK and NPWP gets no valid tax number.

## Limits
- The client invoice is found by the close's and the run's numbers in its notes (an invoice cannot link a module it
  does not know); nobody edits those notes by hand (`invoice_payroll_tag_lock`).
- A flow cannot loop or submit: one slip per recap, slips submitted one by one, the invoice is a draft.
- Submittable records are renamed from DRAFT- at submit without their links following, so the run (linked by slips)
  is not submittable and is closed by its own document.
- The transfer list is a list export, not a print (prints are HTML and XML only, contract 0029). The web app has no
  download button yet (contract 0024 §7, rangka-os); until then the file comes from the export URL above.
- No extra BPJS family members, no gross-up. Slips are submitted one by one (a bulk submit needs the web app: card in the
  board).
