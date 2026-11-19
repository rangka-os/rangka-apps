#!/usr/bin/env python3
"""Writes modules/payroll/flows/payroll_run_fill.rk, payroll_run_fill_on_create.rk and payslip_from_attendance.rk:
one fill, three triggers.

    python3 scripts/gen_payroll_fill_flows.py

payroll_run_fill and payroll_run_fill_on_create run when finance saves an internal payroll run with "Isi slip dari
absen" ticked (on an update or at create); they run as that person. payslip_from_attendance runs every morning as the
system, once per open internal run (auto_filled), when the period is over, no staff attendance period over it is still
a draft and, with the attendance module, the submitted summaries reach the period's first and last day.

The fill of one run (rangka-apps#101 slice b):
- a new draft payslip (from_fill) per active worker with pay data whose employee record (worker.employee) has a
  non-cancelled attendance_summary inside the run's period (of the run's outlet when it names one); a daily worker needs
  at least one day present (a 0-day slip would leave the employee's BPJS share as a negative net pay, refused, and the
  whole fill with it). On a run of every outlet also each monthly worker without a summary (an owner or manager who does
  not clock in), except on a site with the projects module, where monthly workers may be outsourced staff paid by
  client runs. A worker with a live slip in the tax month (this run or another) gets no new one.
- a draft the fill made earlier in this run whose summary link is empty or no longer valid (the attendance period was
  cancelled, or the slip was filled before the attendance was submitted) is refreshed from the current summaries. A
  draft finance made by hand, or one linked to a valid summary, is never rewritten.
- fill_note: new, refreshed, workers skipped because they already have a slip in another run of the tax month (days at
  a second outlet are then not paid by this run), and summaries left out because they straddle the period's start or end.
Do not edit the generated flows by hand: change this script and run it again.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
out = os.path.join(ROOT, "modules", "payroll", "flows")

# HAS(p): the worker of profile p has attendance that pays: a summary (monthly) or at least one day present (daily)
HAS = ("w.employee != '' && (p.pay_basis == 'monthly' ? sums.exists(s, s.employee == w.employee)"
       " : sums.filter(s, s.employee == w.employee).map(s, s.days_present).sum() > 0)")
FALLBACK = "(run.outlet == '' && p.pay_basis == 'monthly' && !app_installed('projects'))"
ONE_PROFILE = "profs.filter(q, q.worker == p.worker)[0].name == p.name"
NEW = (f"workers.exists(w, w.name == p.worker && (({HAS}) || {FALLBACK}))"
       f" && !taken.exists(t, t.worker == p.worker) && {ONE_PROFILE}")
REFRESH = (f"workers.exists(w, w.name == p.worker && ({HAS}))"
           " && own.exists(d, d.worker == p.worker && !sums.exists(s, s.name == d.attendance_summary))")
SKIPPED = (f"workers.exists(w, w.name == p.worker && ({HAS}))"
           " && taken.exists(t, t.worker == p.worker && t.payroll_run != run.name)")
EMP = "workers.filter(w, w.name == p.worker)[0].employee"
MINE = f"sums.filter(s, s.employee == {EMP})"
ATTENDANCE = f'''attendance_summary: "size({MINE}) > 0 ? {MINE}[size({MINE}) - 1].name : ''"
days: "{MINE}.map(s, s.days_present).sum()"
late_min_total: "{MINE}.map(s, s.late_min_total).sum()"
overtime_hours: "{MINE}.map(s, s.overtime_hours).sum()"'''


def indent(text, n):
    return "".join((" " * n + ln if ln else ln) + "\n" for ln in text.splitlines())


def reads():
    """The period's summaries (inside it, and the ones straddling an end) or empty lists without the module."""
    return '''- if: "app_installed('attendance')"
  then:
    - find_all: attendance_summary
      where: "!cancelled && period_from >= run.period_from && period_to <= run.period_to && (run.outlet == '' || outlet == run.outlet)"
      sort: period_from
      as: sums
    - find_all: attendance_summary
      where: >-
        !cancelled && period_from <= run.period_to && period_to >= run.period_from && (run.outlet == '' || outlet == run.outlet)
        && (period_from < run.period_from || period_to > run.period_to)
      as: straddle
  else:
    # no attendance module: empty lists (a find on a model that is not installed fails)
    - find_all: payslip
      where: "name == ''"
      as: sums
    - find_all: payslip
      where: "name == ''"
      as: straddle
'''


def fill(auto):
    """The fill of one internal run `run` (a variable), then the run's note (and auto_filled for the morning flow)."""
    done = "\n    auto_filled: 'true'" if auto else ""
    return f'''- find_all: payroll_profile
  where: active
  sort: name
  as: profs
- find_all: worker
  where: active
  as: workers
# every live slip of the tax month: one slip per worker per tax month (the slip's key), so a worker who has one, in
# this run or another, gets no new one
- find_all: payslip
  where: "tax_year == run.tax_year && tax_month == run.tax_month && docstatus != 2"
  as: taken
# the drafts this fill made earlier in this run (from_fill): refreshed when their summary is gone or was never there
- find_all: payslip
  where: "payroll_run == run.name && docstatus == 0 && from_fill"
  as: own
- for_each: profs
  as: p
  do:
    - if: "{NEW}"
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
            from_fill: 'true'
{indent(ATTENDANCE, 12)}            ytd_gross: "prev.map(x, x.gross_taxable).sum()"
            ytd_pph21: "prev.map(x, x.pph21).sum()"
            ytd_pension: "prev.map(x, x.jht_employee + x.jp_employee).sum()"
            ytd_months: "size(prev)"
            open_advance: "kas.exists(k, repaid.map(x, x.cash_advance == k.name ? x.other_deduction : 0).sum() < k.amount) ? kas.filter(k, repaid.map(x, x.cash_advance == k.name ? x.other_deduction : 0).sum() < k.amount)[0].name : ''"
    - if: "{REFRESH}"
      then:
        - find: payslip
          where: payroll_run == run.name && worker == p.worker && docstatus == 0 && from_fill
          as: d
        - update: d
          set:
{indent(ATTENDANCE, 12)}- update: run
  set:
    fill_from_attendance: 'false'{done}
    fill_note: >-
      string(size(profs.filter(p, {NEW}))) + ' slip baru, '
      + string(size(profs.filter(p, {REFRESH}))) + ' slip diperbarui, '
      + string(size(profs.filter(p, {SKIPPED}))) + ' karyawan sudah punya slip di periode lain bulan pajak ini, '
      + string(size(sums)) + ' rekap absen dibaca, ' + string(size(straddle)) + ' rekap melewati awal atau akhir periode tidak dibaca, '
      + ('as_of' in event ? event.as_of : today())
'''


HDR = "# yaml-language-server: $schema=https://rangka-os.github.io/rangka-os/schema/0.5/flow.json\n"
MANUAL_DOC = '''# Finance saves an internal payroll run with "Isi slip dari absen" ticked: draft payslips from the staff attendance
# summaries of the period, as described in scripts/gen_payroll_fill_flows.py, which writes this file (edit the script,
# not this file). The tick is cleared and fill_note says what was done. Runs as the person saving (finance_manager or
# the owner) and only makes or refreshes drafts: finance checks and submits them. The same fill runs once by itself the
# morning after the period's attendance is complete (payslip_from_attendance).
'''
DESC = "description: {id: 'Saat periode gaji karyawan sendiri disimpan dengan centang \"Isi slip dari absen\", slip gaji draft dibuat dari rekap absen karyawan periode itu, juga untuk karyawan bulanan yang tidak absen. Slip yang diketik sendiri tidak diubah.'}\n"
STEPS = '''steps:
  - find: payroll_run
    where: name == event.name
    as: run
''' + indent(reads() + fill(False), 2)

manual = HDR + MANUAL_DOC + '''name: payroll_run_fill
label: {id: Isi slip gaji dari absen, en: Fill payslips from attendance}
''' + DESC + '''on: payroll_run.updated
if: "event.fill_from_attendance && event.run_kind == 'internal' && !event.closed"
''' + STEPS

on_create = HDR + MANUAL_DOC + '''# This one is the same fill for a run saved with the tick when it is created (a flow has one trigger).
name: payroll_run_fill_on_create
label: {id: Isi slip gaji dari absen saat periode dibuat, en: Fill payslips from attendance on create}
''' + DESC + '''on: payroll_run.created
if: "event.fill_from_attendance && event.run_kind == 'internal' && !event.closed"
''' + STEPS

auto = HDR + '''# Every morning at 07:00 (before payroll_reminder at 07:15, which then sees the slips), and by the owner or finance from
# the flow list: each open internal payroll run not yet filled by this flow (auto_filled), whose period is over, that no
# draft staff attendance period overlaps (its outlet's or the whole business's) and, with the attendance module, whose
# submitted summaries reach the period's first and last day, gets the fill described in scripts/gen_payroll_fill_flows.py
# (which writes this file), once: drafts deleted or slips cancelled afterwards are not made again. Runs as the system;
# it makes or refreshes drafts only and never submits, closes or pays. The day is today, or event.as_of (YYYY-MM-DD)
# when run by hand.
name: payslip_from_attendance
label: {id: Slip gaji dari rekap absen karyawan, en: Payslips from staff attendance}
description: {id: 'Tiap pagi, periode gaji karyawan sendiri yang sudah lewat dan rekap absennya sudah lengkap diisi slip gaji draft dari rekap absen, sekali per periode. Slip tidak pernah diajukan atau dibayar otomatis.'}
schedule: '0 7 * * *'
steps:
  - find_all: payroll_run
    where: "run_kind == 'internal' && !closed && !auto_filled && period_to < ('as_of' in event ? event.as_of : today())"
    sort: period_to
    limit: 100
    as: runs
  - for_each: runs
    as: run
    do:
''' + indent('''- if: "app_installed('attendance')"
  then:
    - find_all: attendance_period
      where: "docstatus == 0 && period_from <= run.period_to && period_to >= run.period_from && (run.outlet == '' || only_outlet == '' || only_outlet == run.outlet)"
      as: open_periods
  else:
    - find_all: payslip
      where: "name == ''"
      as: open_periods
''' + reads() + '''# with the attendance module: no draft period over the run, and the submitted summaries reach both ends of the period
- if: "size(open_periods) == 0 && (!app_installed('attendance') || (sums.exists(s, s.period_from == run.period_from) && sums.exists(s, s.period_to == run.period_to)))"
  then:
''' + indent(fill(True), 4), 6)

for name, text in (("payroll_run_fill", manual), ("payroll_run_fill_on_create", on_create), ("payslip_from_attendance", auto)):
    with open(os.path.join(out, name + ".rk"), "w") as f:
        f.write(text)
