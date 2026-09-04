# Project M.I.R.A — Interview Guide C: Data / ETL Lead

**Researcher:** Mira Dalal · MSc Advanced Computer Science · Student ID 19373191
**Supervisor:** Dr Samia Kamal
**Role of this participant:** Engineering expert — how transforms are designed, where schema mapping fails, and how ETL pipelines handle data loss and drift. Informs the design and evaluation, NOT the SMB-demand premise.

> Confirm ethics route with Dr Kamal first. Invite them to skip anything proprietary. His context is large-scale batch ML pipelines — take his failure-mode insights, leave the architecture (your scope is real-time, webhook-driven, small-scale).

---

## Consent script
"Thanks for your time. I'm a Master's student researching how data integrity holds up when records are transformed and moved between systems. I'm interested in your data-engineering perspective on where transforms and schema mapping break. About 30 minutes. With permission I'd like to record it only for accurate notes; stored securely, used only for this dissertation, anonymised, deleted after marking. Please skip anything proprietary. Voluntary — stop anytime; withdraw up to [date]."

Consent to take part: ☐ Yes ☐ No  ·  Consent to record: ☐ Yes ☐ No
Signature / verbal confirmation: ____________  Date: ______

---

## Questions

**Schema mapping & transforms**
1. When you model data from a spec into a target schema, where do mismatches with the source most often bite — types, nullability, nested structures, units?
2. How do you decide and document the mapping rules between a source and target schema?

**Partial failure & data loss**
3. In your ETL, how do you handle a record that only partially transforms — fail the batch, drop the field, default it, quarantine it? Why that choice?
4. How do you detect silent data loss during a load — or do you find out downstream when the model behaves oddly?
5. What's your strategy for idempotency and retries when a load fails midway?

**Schema drift (a failure mode worth surfacing)**
6. How do you handle schema drift — when the source changes structure without warning? How do you detect it before it corrupts the destination?

**Adversarial (feeds design + evaluation)**
7. If you were designing a transform layer that had to guarantee ≥99% field-mapping accuracy across changing schemas, what would you watch out for? Where would it break first?

---
*Notes: Q6 (schema drift) and Q7 feed directly into your design and evaluation chapters. Resist his pull toward batch/warehouse/Airflow framing — extract failure modes, not architecture.*
