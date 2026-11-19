#!/usr/bin/env python3
"""Writes modules/payroll/flows/payroll_run_fill.rk and payslip_from_attendance.rk: one fill, two triggers.

    python3 scripts/gen_payroll_fill_flows.py

payroll_run_fill runs when finance ticks "Isi slip dari absen" on an internal payroll run and saves it; it runs as that
person. payslip_from_attendance runs every morning as the system for each open internal run whose period is over and
whose staff attendance periods are all submitted. Both make, for one run, a draft payslip per active worker with pay
data whose employee record has a staff attendance summary inside the period (of the run's outlet when it names one),
and on a run of every outlet also per monthly worker without one; a worker who already has a live slip in the tax month
is skipped. A flow has one trigger, so the steps are written here once (rangka-apps#101 slice b).
Do not edit the generated flows by hand: change this script and run it again.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "modules", "payroll", "flows")

def body(ind):
    """The fill of one internal run `run` (a variable): read the period's summaries, the pay data and the slips already
    made in the tax month, then one draft slip per worker due one."""
    # COND(p): this profile gets a new slip in this run
    has = "w.employee != '' && sums.exists(s, s.employee == w.employee)"
    cond = ("workers.exists(w, w.name == p.worker && (" + has + " || (run.outlet == '' && p.pay_basis == 'monthly')))"
            " && !taken.exists(t, t.worker == p.worker) && profs.filter(q, q.worker == p.worker)[0].name == p.name")
    emp = "workers.filter(w, w.name == p.worker)[0].employee"
    mine = f"sums.filter(s, s.employee == {emp})"
    L = f'''- if: "app_installed('attendance')"
  then:
    - find_all: attendance_summary
      where: "!cancelled && period_from >= run.period_from && period_to <= run.period_to && (run.outlet == '' || outlet == run.outlet)"
      sort: period_from
      as: sums
  else:
    # no attendance module: an empty list (a find on a model that is not installed fails)
    - find_all: payslip
      where: "name == ''"
      as: sums
- find_all: payroll_profile
  where: active
  sort: name
  as: profs
- find_all: worker
  where: active
  as: workers
# every live slip of the tax month: one slip per worker per tax month (the slip's key), so a worker who has one, in
# this run or another, is skipped instead of refused
- find_all: payslip
  where: "tax_year == run.tax_year && tax_month == run.tax_month && docstatus != 2"
  as: taken
- for_each: profs
  as: p
  do:
    - if: "{cond}"
      then:
        - find_all: cash_advance
          where: worker == p.worker && docstatus == 1
          sort: advance_date
          as: kas
        # Year to date as payslip_from_recap and payslip_ytd_on_create read it (a flow's writes start no flows): a
        # permanent employment that ended earlier this year and was followed by casual months starts a new period.
        - find_all: payslip
          where: worker == p.worker && tax_year == run.tax_year && tax_month < run.tax_month && docstatus == 1 && employment == 'casual' && pay_basis == 'daily'
          sort: -tax_month
          limit: 1
          as: lastcas
        - find_all: payslip
          where: >-
            worker == p.worker && tax_year == run.tax_year && tax_month < run.tax_month && docstatus == 1 && last_month && !(employment == 'casual' && pay_basis == 'daily')
            && tax_month < (size(lastcas) > 0 ? lastcas[0].tax_month : 0)
          sort: -tax_month
          limit: 1
          as: ended
        - find_all: payslip
          where: >-
            worker == p.worker && tax_year == run.tax_year && tax_month < run.tax_month && docstatus == 1 && !(employment == 'casual' && pay_basis == 'daily')
            && tax_month > (size(ended) > 0 ? ended[0].tax_month : 0)
          as: prev
        - find_all: payslip
          where: worker == p.worker && cash_advance != '' && docstatus == 1
          as: repaid
        # Days present, minutes late and overtime hours are the sums of the worker's summaries in the period. Unpaid
        # days stay 0 (absence is not unpaid leave until leave is recorded, rangka-apps#114) and the days with
        # overtime stay 0: a summary has no count of them, so without a weighted overtime rate every hour is paid 2x
        # (PP 35/2021, never less than due) until finance enters them on the draft.
        - create: payslip
          set:
            payroll_run: run.name
            profile: p.name
            attendance_summary: "size({mine}) > 0 ? {mine}[size({mine}) - 1].name : ''"
            days: "{mine}.map(s, s.days_present).sum()"
            late_min_total: "{mine}.map(s, s.late_min_total).sum()"
            overtime_hours: "{mine}.map(s, s.overtime_hours).sum()"
            ytd_gross: "prev.map(x, x.gross_taxable).sum()"
            ytd_pph21: "prev.map(x, x.pph21).sum()"
            ytd_pension: "prev.map(x, x.jht_employee + x.jp_employee).sum()"
            ytd_months: "size(prev)"
            open_advance: "kas.exists(k, repaid.map(x, x.cash_advance == k.name ? x.other_deduction : 0).sum() < k.amount) ? kas.filter(k, repaid.map(x, x.cash_advance == k.name ? x.other_deduction : 0).sum() < k.amount)[0].name : ''"
- update: run
  set:
    fill_from_attendance: 'false'
    fill_note: "string(size(profs.filter(p, {cond}))) + ' slip baru, ' + string(size(sums)) + ' rekap absen dibaca, ' + ('as_of' in event ? event.as_of : today())"
'''
    return "".join(ind + ln + "\n" if ln else "\n" for ln in L.splitlines())

HDR = "# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/flow.json\n"
fill = HDR + '''# Finance ticks "Isi slip dari absen" on an internal payroll run and saves it: one draft payslip per active worker with
# pay data whose employee record has a staff attendance summary (module attendance) inside the run's period, of the
# run's outlet when it names one; on a run of every outlet also each monthly worker without a summary (an owner or a
# manager who does not clock in), so the run holds the whole staff. The tick is cleared and fill_note says how many slips
# were made. Workers who already have a live slip in the tax month are skipped, so ticking again only adds the missing.
# Runs as the person saving (finance_manager or the owner) and only makes drafts: finance checks and submits them. The
# same steps run every morning for runs whose period is over (payslip_from_attendance); both are written by
# scripts/gen_payroll_fill_flows.py (edit the script, not this file). A summary straddling the run's start or end is not
# read.
name: payroll_run_fill
label: {id: Isi slip gaji dari absen, en: Fill payslips from attendance}
description: {id: 'Saat periode gaji karyawan sendiri dicentang "Isi slip dari absen" dan disimpan, slip gaji draft dibuat dari rekap absen karyawan periode itu, juga untuk karyawan bulanan yang tidak absen. Slip yang sudah ada tidak diubah.'}
on: payroll_run.updated
if: "event.fill_from_attendance && event.run_kind == 'internal' && !event.closed"
steps:
  - find: payroll_run
    where: name == event.name
    as: run
''' + body("  ")

auto = HDR + '''# Every morning at 07:00 (before payroll_reminder at 07:15, which then sees the slips), and by the owner or finance from
# the flow list: each internal payroll run that is not closed, whose period is over and that no draft staff attendance
# period overlaps (its outlet's or the whole business's), gets the draft payslips payroll_run_fill would make. Runs as
# the system, so the summaries are read whoever submitted them; it makes drafts only and never submits, closes or pays.
# Ticking "Isi slip dari absen" does the same at once. Written by scripts/gen_payroll_fill_flows.py. The day is today, or event.as_of (YYYY-MM-DD) when run by hand.
name: payslip_from_attendance
label: {id: Slip gaji dari rekap absen karyawan, en: Payslips from staff attendance}
description: {id: 'Tiap pagi, periode gaji karyawan sendiri yang sudah lewat dan rekap absennya sudah diajukan semua diisi slip gaji draft dari rekap absen. Slip tidak pernah diajukan atau dibayar otomatis.'}
schedule: '0 7 * * *'
steps:
  - find_all: payroll_run
    where: "run_kind == 'internal' && !closed && period_to < ('as_of' in event ? event.as_of : today())"
    sort: period_to
    limit: 100
    as: runs
  - for_each: runs
    as: run
    do:
      - if: "app_installed('attendance')"
        then:
          - find_all: attendance_period
            where: "docstatus == 0 && period_from <= run.period_to && period_to >= run.period_from && (run.outlet == '' || only_outlet == '' || only_outlet == run.outlet)"
            as: open_periods
        else:
          - find_all: payslip
            where: "name == ''"
            as: open_periods
      - if: "size(open_periods) == 0"
        then:
''' + body("          ")
open(out + "/payroll_run_fill.rk", "w").write(fill)
open(out + "/payslip_from_attendance.rk", "w").write(auto)
