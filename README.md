# Project M.I.R.A. (Middleware for Integration and Real-time Automation)

> **Enterprise-Grade Integration Middleware Connecting Monday.com and Xero Accounting**  
> *MSc Advanced Computer Science Dissertation Project - Researcher: Mira Dalal (ID: 19373191) · Supervisor: Dr Samia Kamal*

---

## Overview

**Project M.I.R.A.** is a high-throughput, schema-resilient iPaaS (Integration Platform as a Service) middleware designed to eliminate silent data corruption and finance discrepancies when transferring business operations data between **Monday.com** (CRM/Work OS) and **Xero** (Cloud ERP/Invoicing).

### Key Highlights
- **Sub-Millisecond Processing:** 0.156 ms median transform latency with 3,320+ records/sec throughput.
- **100% Transformation Accuracy:** Exceeds the target threshold ($\ge 99.0\%$) with zero dropped fields.
- **Active Data Validation & Quarantine:** Pydantic v2 domain models reject malformed records with structured diagnostics without halting valid batch items.
- **Intelligent Entity Grouping:** Aggregates individual tasks/subitems into consolidated multi-line accounts receivable (`ACCREC`) invoices.
- **Bidirectional Synchronisation:** Syncs Monday deals $\rightarrow$ Xero Invoices and Xero Contacts $\rightarrow$ Monday CRM boards.
- **Interactive Management Suite:** Real-time Server-Sent Events (SSE) terminal, dynamic schema mapping studio, and RBAC user management.

---

## Detailed Documentation

For exhaustive architectural diagrams, data dictionaries, research grounding, and module breakdowns, please refer to:
📖 **[PROJECT_DOCUMENTATION.md](PROJECT_DOCUMENTATION.md)**

---

## Quick Start

### 1. Setup Environment
```bash
python -m venv .venv
.venv\Scripts\activate       # On Windows
# source .venv/bin/activate  # On Linux/macOS

pip install -r requirements.txt
cp .env.example .env
```

### 2. Run the Application

#### Option A: FastAPI Production Microservice (Recommended)
```bash
uvicorn main.py:app --reload --host 0.0.0.0 --port 8000
```
- Web Application: [http://localhost:8000/sync](http://localhost:8000/sync)
- Interactive API Docs: [http://localhost:8000/docs](http://localhost:8000/docs)

#### Option B: Flask Multi-Page & Webhook Server
```bash
python app.py
```
- Web Application: [http://localhost:5000/](http://localhost:5000/)

### 3. Default Login
- **Email:** `admin@mira.com`
- **Password:** `Admin123!`

### 4. Run Tests & Benchmarks
```bash
# Run unit & integration tests
python -m unittest discover -s . -p "test_*.py"

# Run performance benchmarking engine
python benchmark.py --concurrency 2
```
