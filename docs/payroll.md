# Payroll (module `payroll`)

Monthly pay of outsourced workers (manpower, pilot A) from the attendance recap, the deposits of the month, the margin
of the client contract and the client invoice. Installed with `jasa_proyek`; demo site `demo-manpower`, tests
`tests/day_one_payroll.py` against the hand-written oracle `tests/payroll_oracle.py`.

## Flow of one month
1. Finance keeps one **Data gaji pekerja** (`payroll_profile`) per worker: NIK/NPWP, PTKP status (TER category A/B/C),
   pay basis (daily: pay per day x days present; monthly: fixed salary), fixed allowance, allowance per day present,
   BPJS programmes.
2. Finance opens a **Periode gaji** (`payroll_run`) per client contract (project) and cut-off period: tax year and
   month (the month paid), UMK of the site, JKK rate, JP and BPJS Kesehatan wage caps, how the client is billed.
3. The project admin imports and submits the attendance recap (`attendance_recap`, rangka-apps#28). Each submitted
   recap of a worker with a profile becomes a draft **Slip gaji** (`payslip`) of the open run of that project.
4. Finance adjusts drafts (overtime days for PP 35, THR months, other income, deductions) and submits the slips; the
   run's totals follow every submit and cancel.
5. Finance submits a **Tutup periode gaji** (`payroll_close`): the run is closed, the employer cost the recaps did not
   book goes on the project ledger (kind `payroll`), and the client invoice is made as a draft.

## Rules (sources: reports/keputusan-riset/payroll-manpower.md in the project folder)
- PPh 21: TER monthly (PP 58/2023, tables in `payslip.ter_bp`) on gross = cash pay + JKK + JKM + BPJS Kesehatan 4%;
  120% without a tax number; truncated to the rupiah. December (or `last_month`): Pasal 17 on the year (biaya jabatan
  5%, at most 500.000 per month; JHT 2% + JP 1% of the worker; PTKP; PKP rounded down to thousands) minus the earlier
  slips of the year in closed runs; negative = refund on the slip.
- BPJS wage = basic pay + fixed allowance, never below the UMK. JKK 0,24-1,74%, JKM 0,3%, JHT 3,7% + 2%, JP 2% + 1%
  up to the JP cap, BPJS Kesehatan 4% + 1% up to 12.000.000. Rounded half up to the rupiah.
- Overtime: the weighted pay rate per hour when set (worker or assignment), else PP 35/2021 on working days
  (1/173 of the monthly wage, first hour of each overtime day 1,5x, the rest 2x).
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
- Runs of one project never overlap (`payroll_run_overlap_guard_*`).
- A recap used by a submitted slip cannot be cancelled (`recap_cancel_guard`); `timesheet_cost` is computed from it.
- A run with submitted slips keeps its project, period, tax month and rates (`locked_by_slips`, copies on the first
  slip); a closed run keeps what its close froze (`locked_by_close`); runs are never deleted; one run per project and
  start of period.
- Fee only: the margin row takes the recaps' labour cost back off the project (the client funds the wages).

## Limits
- The client invoice is found by the close's and the run's numbers in its notes (an invoice cannot link a module it
  does not know); nobody edits those notes by hand (`invoice_payroll_tag_lock`).
- A flow cannot loop or submit: one slip per recap, slips submitted one by one, the invoice is a draft.
- Submittable records are renamed from DRAFT- at submit without their links following, so the run (linked by slips)
  is not submittable and is closed by its own document.
- No daily TER for casual workers, no holiday overtime multipliers, no extra BPJS family members, no gross-up.
