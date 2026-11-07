# Courses (module `courses`, piece `kelas_kursus`)

For course schools, tutoring centres, online courses and company training. Site: `examples/sites/demo-kursus`
(composed `kelas_kursus` + `faktur_piutang` + `pembukuan`); scenarios: `tests/day_one_courses.py`.

## Flow of a month
1. **Program kursus** (`course_program`): price, number of meetings, how it is usually paid (in full, in 2 to 12
   instalments, or per month), the invoice line item, VAT-exempt or not.
2. **Angkatan** (`course_batch`): start and end, quota, teacher name and account, offline/online/hybrid, place, online
   class link, weekly schedule.
3. **Peserta** (`student`): a person on their own (a customer with their name is made to bill them) or an employee of a
   client company (billed to the company).
4. **Pendaftaran** (`enrollment`): student in a batch, status Daftar, Aktif, Selesai, Keluar. Price and payment terms are
   copied from the program and can be changed (discount, instalments) until the first charge is submitted.
5. **Pertemuan** (`course_session`): one meeting; attendance is filled with the batch's students, all present, and the
   teacher marks who did not come (Izin, Sakit, Tidak hadir).
6. **Tagihan kursus** (`course_charge`): the whole program, instalment k, or month TTTT-BB. Finance submits it; a draft
   sales invoice is made (tag `[TGK-...]` in its notes); finance checks and submits the invoice. Payments, reminders and
   aging are `faktur_piutang`'s; the journal is `accounting`'s (Dr Piutang / Cr Pendapatan, no PPN for education).

## Rules
- **Quota.** Registered and active enrollments take a seat. The enrollment flows recount the batch's seats under the
  flow read lock (contract 0025) and write `seats_taken`; the batch rule `seats_within_quota` refuses an enrollment, a
  status change back to Daftar/Aktif, or a lower quota that would go over. Two enrollments for the last seat at the same
  moment: one is made, the other gets "Kuota angkatan ini sudah penuh" (tested three times in a row).
- **One charge per period.** `period_key` = enrollment | period (| amended_from) is unique, draft or submitted; a
  cancelled charge keeps its key, and nobody but the owner deletes a charge. Because an amended copy's period can be
  edited, `charge_invoice_on_submit` also refuses a submit when the enrollment already has a submitted charge of that
  period or of another payment plan (audit 2026-11-07 H1: an amended copy moved to a billed month, or instalments
  cancelled and the plan switched to full).
- **Instalments add up.** Instalment k < N = round(total / N); instalment N = total - (N - 1) x round(total / N). The
  enrollment's terms lock at the first submitted charge (`terms_locked`), and a charge whose enrollment changed after
  it was saved is refused at submit (`amount_current`). Rules run before the flow read lock, so both are checked again
  in flows (the submit flow recomputes the amount from the enrollment; `enrollment_seats_on_update` refuses a terms
  change once a charge is submitted): a discount racing a submit cannot split the instalments (audit M3).
- **One invoice per charge.** `invoice_course_once` runs on every invoice submit and looks for charge numbers in []
  in its notes (whatever the site's numbering): an invoice naming one charge submits only while that charge is
  submitted, to its payer, with DPP equal to its amount, and with no other submitted invoice carrying the number; an
  invoice naming two charges is refused. Notes that carry a charge number cannot be edited (`invoice_course_tag_lock`).
  A charge cannot be cancelled while its invoice is submitted (`charge_cancel_guard`). An amended charge always gets a
  new draft invoice (its own tax rate and due date); the draft its cancelled original left is marked replaced and can
  only be deleted.
- **Instructor.** Row scope `instructor` = their user record: own batches, sessions and enrollments only; money fields
  hidden (contract 0027), as are students' phone, email, guardian and notes and programs' prices (students and programs
  have no row scope); no charges, invoices or the overview dashboard.

## Known limits
- On a PKP site a VAT-exempt course invoice has tax rate 0 but no Faktur Pajak code 08 (invoicing/coretax, audit L4).
- Attendance does not refuse the same student twice in one session (the CEL validator has no nested lambdas); the
  session flow fills each student once.
- Monthly charges are made one at a time (flows have no loops); a bulk "tagih SPP bulan ini" needs an import or a
  kernel action.
- Teacher pay (honor per meeting) is out of scope; the teacher is a name and a user account, not a `worker`.
