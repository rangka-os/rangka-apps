# Expense claims (module reimbursement)

rangka-apps#117. A staff member pays a cost of the business from their own pocket (parking, fuel, supplies) and claims
it back with the receipt photo. Installed by the staff bundle `karyawan_gaji`. Kasbon (cash advances repaid from pay)
lives in module `payroll`: docs/payroll.md.

## Records

| Model | What | Who |
| --- | --- | --- |
| `expense_category` | Kategori klaim: expense account (accounting), receipt required, limit per claim (0 = none) | finance, head office |
| `expense_claim` | Klaim biaya, `KLM-YYYY-#####`: employee, date, category, amount, what for, receipt photo, outlet, project, paid back with (`payroll`, `cash`, `bank`) | the staff member, their own only |
| `claim_settlement` | whether an approved claim was paid and by what (a submitted claim cannot change) | kept by flows |
| `claim_payment` | finance pays one approved claim from cash or bank, `BKL-YYYY-#####` | finance |
| `reimbursement_guard` | never stored: a flow's refusal | flows |

## Flow

1. The staff member drafts a claim (screen `klaim_biaya`, or the list) and submits it. Rules: amount in whole rupiah
   above 0, within the category's limit, a receipt photo when the category needs one, a date not in the future, only
   for themself (head office may claim for someone else).
2. The submit waits for an approver (contract 0007): `staff_claim` for staff, approvers the outlet supervisor or head
   office; `supervisor_claim` for an outlet supervisor's own claim, approvers head office only (a supervisor holds an
   approver role, so the first policy would approve their own claim). The approver must read the claim: a supervisor
   reads only the claims of their outlets (role row scope), so the supervisor of another outlet cannot approve. Head
   office and the owner approve their own claims at once (self-approval is recorded).
3. Approved = submitted, as the claimant (0007 §2). `claim_settlement_on_submit` makes its `claim_settlement`, due.
   Accounting: Dr the category's expense account (`expense_account`, copied when the claim is saved; 6-1900 without
   one) / Cr 2-1510 Utang reimburse karyawan, with the claim's outlet and project.
4. Paid back:
   - cash or bank: finance submits a `claim_payment`. `claim_payment_on_submit` refuses a claim already paid or
     cancelled and marks it paid by the payment. Accounting: Dr 2-1510 / Cr Kas or Bank. Cancelling the payment gives
     the claim back.
   - with the salary (`pay_with: payroll`): the next slip made for the employee carries the sum of their claims due as
     `payslip.reimbursement`, outside the taxable gross (docs/payroll.md). At the slip's submit
     `claim_settlement_on_payslip_submit` checks that the field still equals the claims due and marks them paid by the
     slip; a slip cancel gives them back. The payroll close debits 2-1510 with the run's reimbursements.
   A claim to be paid with salary may still be paid by a `claim_payment` first; the next slip then leaves it out.
5. Cancel (head office or finance): refused while a payment or a slip paid the claim (`claim_cancel_guard`); otherwise
   the settlement is marked cancelled and the journal is reversed.

The finds on `claim_settlement` take the flow read lock (contract 0025), so a payment and a slip, two payments, or a
cancel and a payment of one claim at the same moment run one after the other and the second is refused.

## Dashboards

- `klaim_kasbon_saya` (staff, karyawan_gaji): own claims waiting for approval, approved and due, kasbon left.
- `gaji_ringkas` (finance): claims due and kasbon left, and the list "Klaim menunggu dibayar".

## Not in this version

- The rebill of a project claim to the client: a `project_expense` posts its own journal (paid from cash) and project
  ledger line, so drafting one from a claim would book the cost twice. It needs a link on `project_expense` that skips
  its cash journal (module projects); follow-up of #117. The claim's journal already carries the project.
- `jasa_proyek` does not install the module yet (follow-up): its kasbon requests work (payroll 2.2).
- Per diem and mileage rules, corporate cards, multi-currency claims.
- Receipt reading by Raka: may suggest category and amount into a draft for the staff member to confirm; it never
  submits or approves (money is never decided by an LLM).
