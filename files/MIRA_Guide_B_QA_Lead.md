# Project M.I.R.A — Interview Guide B: QA Lead

**Researcher:** Mira Dalal · MSc Advanced Computer Science · Student ID 19373191
**Supervisor:** Dr Samia Kamal
**Role of this participant:** Validation expert — how cross-system data transfers are tested, how defects are caught, and how to break the proposed middleware.

> Confirm ethics route with Dr Kamal first. The participant may face employer confidentiality limits — invite them to skip anything proprietary.

---

## Consent script
"Thanks for your time. I'm a Master's student researching how data integrity is maintained when records move between business systems — sales tools, accounting, ERP. I'm interested in your QA perspective on transfer failures. About 30 minutes. With permission I'd like to record it only for accurate notes; stored securely, used only for this dissertation, anonymised, deleted after marking. Please skip anything proprietary or confidential to your employer. Voluntary — stop anytime; withdraw up to [date]."

Consent to take part: ☐ Yes ☐ No  ·  Consent to record: ☐ Yes ☐ No
Signature / verbal confirmation: ____________  Date: ______

---

## Questions

**Validation practice**
1. In your systems, how do you currently verify that data moved between two platforms is correct? What's automated, what's manual?
2. At what point do you check integrity — at source, in transit, at destination, or after the fact?
3. Do you have a definition of a "correct" transfer? How is it specified or tested?

**Defect classes (maps to your confusion-matrix)**
4. What classes of data-transfer defects have you actually seen — dropped fields, type mismatches, truncation, silent skips, encoding issues? Which are most common?
5. Which are hardest to detect, and why?
6. How often do defects reach production before anyone notices, and how are they usually found?

**Failure behaviour**
7. When a transfer fails partway, what does the system do — fail loud, retry, log, roll back, or silently continue?
8. How are partial failures and idempotency handled when a request is retried?

**Adversarial (gold for evaluation chapter)**
9. If you were testing a middleware claiming ≥99% field-mapping accuracy, how would you try to break it? What inputs would you throw at it?
10. What edge cases in real payloads most often expose weak mapping logic?

---
*Notes: Q4/Q6 cross-check the silent-failure premise from the technical side. Q9 hands you adversarial test inputs — capture verbatim.*
