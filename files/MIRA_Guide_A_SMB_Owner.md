# Project M.I.R.A — Interview Guide A: SMB Owner / CEO

**Researcher:** Mira Dalal · MSc Advanced Computer Science · Student ID 19373191
**Supervisor:** Dr Samia Kamal
**Role of this participant:** Demand signal — establishes whether silent, finance-affecting data corruption between systems is a real, felt problem.

> Confirm ethics route with Dr Kamal first. Note in the write-up that this participant is your employer; acknowledge the resulting bias and the power relationship.

---

## Consent script
"Thanks for your time. I'm a Master's student researching where data goes wrong when it moves between business software — for example between a CRM and an accounting package. I'm not selling anything; there are no right answers. About 20–30 minutes. With permission I'd like to record it only for accurate notes; stored securely, used only for this dissertation, anonymised, and deleted after marking. Participation is entirely voluntary — you can skip anything, stop anytime, and it won't affect anything at work. You can withdraw your data up to [date]."

Consent to take part: ☐ Yes ☐ No  ·  Consent to record: ☐ Yes ☐ No
Signature / verbal confirmation: ____________  Date: ______

---

## Questions

**Context**
1. Walk me through what happens from winning a customer to that sale appearing in your accounts. Who touches it, what tools are involved?
2. Where does information get typed in more than once?
3. Which two systems don't talk to each other that you wish did?

**Failures (spend most time here)**
4. Tell me about the last time a sale, invoice, or customer record showed up wrong in one system after coming from another. What was wrong?
5. How did you find out it was wrong — did the system warn you, or did you discover it later?
6. Has anything to do with tax, VAT, currency, or totals ever come across incorrectly? What happened?
7. When data goes wrong, how long to notice and fix, and who fixes it?
8. Has a sync ever silently skipped a line item, field, or whole record that you only caught later?

**Coping & stakes**
9. If you use Zapier, Make, or a built-in connector, what does it *not* handle that you still do by hand?
10. How do you currently check that what moved between systems is correct — is there any check at all?
11. What's the worst consequence you've had, or fear, from data being wrong between systems?
12. *(Last only)* If a tool guaranteed it caught and flagged every error before it hit your accounts, would that matter — or is it not a real problem?

**Settle the build decision**
13. Which accounting/ERP system do you actually use? (Helps decide the target platform: Xero / QuickBooks / Odoo.)

---
*Notes: Q5/Q8 test the silent-failure premise. If she says errors are obvious and rare, that's a finding — record it honestly. Capture verbatim where possible.*
