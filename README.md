# CS411 Project #1: Ollama

CS411 Software Architecture Design (2026-2027 Fall), Project #1.
Selected open-source project: [Ollama](https://github.com/ollama/ollama).

We examine Ollama's current architecture, propose an extension (a Smart Router), and implement a new login page with an IAM module in Python.

## Repository structure

```
cs-411-project/
├── SOURCES.md           # References used in the report
├── diagrams/            # Ollama's current architecture (figures 1-6)
├── updated_diagrams/    # Architecture after our IAM + Smart Router extension (figures 7-12)
└── task4-iam-gateway/   # Task 4: login page + IAM module implementation (Python)
```

### `diagrams/`: current architecture

UML diagrams of Ollama as it is today (4+1 views):

| Figure | View | Diagram |
|---|---|---|
| Fig. 1 | Scenario | Use case |
| Fig. 2 | Logical | Class diagram |
| Fig. 3 | Development | Package diagram |
| Fig. 4 | Process | Runner state machine |
| Fig. 5 | Process | Chat request sequence |
| Fig. 6 | Physical | Deployment |

### `updated_diagrams/`: architecture after the extension

The same views, updated for our IAM + Smart Router extension (Fig. 7 to Fig. 12).

| Figure | View | File |
|---|---|---|
| Fig. 7 | Scenario | `fig7_use_case.png` |
| Fig. 8 | Logical | `fig8_class_logical.png` |
| Fig. 9 | Development | `fig9_package_development.png` |
| Fig. 10 | Process | `fig10_runner_state.png` |
| Fig. 11 | Process | `fig11_sequence_chat.png` |
| Fig. 12 | Physical | `fig12_deployment.png` |

### `task4-iam-gateway/`: implementation

A Python gateway placed in front of Ollama. It adds a login page, user and API key
management, role-based access control and an audit log, without changing Ollama's code.

```
task4-iam-gateway/
├── ollama_iam_gateway/
│   ├── iam/        # Authentication, sessions, API keys, RBAC policy, audit
│   ├── gateway/    # Authenticated reverse proxy to Ollama
│   ├── web/        # Login page and admin/account pages (templates, static files)
│   ├── data/       # SQLite models and repositories
│   ├── cli.py      # Admin command line
│   └── main.py     # Application entry point
├── tests/          # pytest suite
├── scripts/        # Demo helper scripts
└── docs/           # Task 4 diagrams and screenshots
```

Installation, running and demo instructions are in
[task4-iam-gateway/README.md](task4-iam-gateway/README.md).
