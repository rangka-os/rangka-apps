---
name: rangka-app-karyawan-gaji
description: "Rangka Karyawan dan Gaji (app karyawan_gaji): a business paying its own staff (no client contract): pay data per employee, staff attendance into draft payslips with BPJS and PPh 21 TER, kasbon repaid by monthly installments, expense claims paid back untaxed, salary transfer file. Use when an owner wants payroll for their own employees (warung, toko, klinik, kantor) and for the first day after it is built (first-day.json). Outsourcing firms billing a client use jasa_proyek."
metadata: {rangka: {version: 0.1.0, app: karyawan_gaji, requires: [rangka-compose, rangka-changesets]}}
---

# Rangka Karyawan dan Gaji

Read `rangka-compose` first. This app composes `users`, `workforce`, `outlets`, `attendance`, `payroll` (2.2, internal
runs) and `reimbursement`. Install `absen_karyawan` next to it for the clock screen and the attendance dashboards.

## How pay flows

1. Each employee has a worker record (who is paid) and, when they clock in, an employee record in Tim, linked by
   `worker.employee`. Pay data (`payroll_profile`) holds salary, allowances, PTKP, BPJS and the bank account.
2. Staff clock in with `attendance`; head office submits an `attendance_period`, which writes one
   `attendance_summary` per employee.
3. Finance opens an internal `payroll_run` (no project) and ticks "Isi slip dari absen": draft payslips are made from
   the summaries (`payroll_run_fill`). Every morning `payslip_from_attendance` does the same for runs whose period is
   over. Unpaid days are never guessed from absence: finance types them.
4. Finance checks and submits the slips, closes the run (`payroll_close`), then downloads the transfer list
   (dashboard `transfer_gaji`, or the payslip list export with the transfer columns). No bank API.
5. Kasbon (rangka-apps#117): staff ask from their phone (screen `ajukan_kasbon`, a draft `cash_advance`); finance picks
   the worker and submits it, which pays it out. Interest-free, one open kasbon per worker; the slips deduct its
   monthly installment by themselves until `cash_advance_balance` shows it repaid.
6. Expense claims: staff claim with a receipt photo (screen `klaim_biaya`); the outlet supervisor or head office
   approves; finance pays it from cash or bank (`claim_payment`) or it rides on the next slip as `reimbursement`,
   outside the taxable gross. docs/reimbursement.md.
7. Every employee, whatever their role, opens dashboard `saya` from the phone (or the "Slip gaji" shortcut of the
   installed app): their own payslips, attendance, kasbon, claims and coming shifts (member grants, rangka-os contract
   0074). A slip shows there once released: submitted, and its run closed or its pay date reached.

## First day (first-day.json)

Read by Rangka Cloud after the build. `seed` prepares workers with their pay data only from what the owner said, each
with the owner's quote; money fields are shown separately and nothing is written before the owner agrees. Never invent
salaries, NIK, NPWP, PTKP or account numbers. Say steps in the owner's words; never call Juragan an app.

## Rules to say when asked

- BPJS and PPh 21 follow docs/payroll.md (PP 44/2015, 45/2015, 46/2015, Perpres 82/2018, PP 58/2023, PMK 168/2023).
- Lateness is deducted only when the run sets a rate per minute (default off).
- The price of this app in Juragan is not decided here.
