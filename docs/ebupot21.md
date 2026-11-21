# PPh 21 Coretax exports (module payroll 2.4, rangka-apps#128)

Owner flow, v1 (manual upload, no PJAP partner): open **Masa PPh 21**, add the month, download the XML you need from the print menu, upload it in Coretax.

**Format to verify against the DJP template before first filing.** The element names below follow the DJP templates (`bpmp.xml`, `bp21.xml`, `bpa1.xml`, pajak.go.id Coretax "template XML dan converter Excel ke XML") as far as the official guide text allows; the template files could not be fetched when this was built. Research and sources: `reports/keputusan-riset/ebupot21.md` in the project folder.

## Models
- `pph21_period` (Masa PPh 21): year, month, employer NPWP (16), NITKU (22), withholding date. Employer values default from the org NPWP.
- `pph21_annual` (Rekap PPh 21 setahun): one row per worker per period of work, made by flow `pph21_annual_on_slip_submit` when the last-tax-month slip (December or the leaving month) is submitted; voided by `pph21_annual_on_slip_cancel`. Every amount is copied from slips, nothing is recomputed. Staff read and print their own row (member grant) from the slip's pay date.
- `payslip` copies `slip_nik` and `slip_npwp` from the profile (hidden). Slips submitted before 2.4 have them empty and block the export with a message (make those in Coretax).

## Prints
| Print | Rows | Coretax root |
| --- | --- | --- |
| `coretax_bpmp` | permanent, not last month, not daily TER | `MmPayrollBulk/ListOfMmPayroll/MmPayroll` |
| `coretax_bp21` | daily TER slips, three bands | `Bp21Bulk/ListOfBp21/Bp21` |
| `coretax_bpa1` | `pph21_annual` rows with `month_end` = month, not void | `A1Bulk/ListOfA1/A1` |
| `bukti_potong_a1` | one `pph21_annual` row (HTML/PDF for the employee, a summary, not the official form) | |

## Field map
BPMP: TaxPeriodMonth/Year, CounterpartOpt Resident, CounterpartTin (16-digit NPWP, 15-digit with leading 0, else NIK), StatusTaxExemption (TK/0, K/1), Position, TaxObjectCode 21-100-01, Gross (slip bruto), Rate (TER percent x120% without tax number, 4 decimals), IDPlaceOfBusinessActivity (NITKU), WithholdingDate (YYYY-MM-DD).
BP21: Gross, Deemed (100, or 50 above 2.500.000 a day), Rate, TaxObjectCode 21-100-24 (0% and 0,5% bands) or 21-100-30, Document Other + slip number.
BPA1: TaxPeriodMonthStart/End, StatusOfWithholding (FullYear = January to December, else PartialYear), SalaryPensionJhtTht, OtherBenefit, InsurancePaidByEmployer, TantiemBonusThr, PensionContributionJhtThtFee; Coretax computes the annual tax itself, Article21IncomeTax is 0.
Numbers are plain integers (rupiah), dates ISO, rates with a dot.

## Rules
- Casual daily workers never get an A1 (monthly BP21 only). Joiners mid-year are `partial_year` with actual income and full PTKP.
- Tests: `tests/day_one_ebupot21.py` (demo-manpower) against `tests/payroll_oracle.py` `slip` and `annual`: full year, June leaver, NIK-only worker, casual worker, a no-ID month refused, void on cancel.
