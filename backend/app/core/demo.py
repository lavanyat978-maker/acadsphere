"""Demo account fixtures.

These credentials are only seeded when DEMO_MODE is enabled and are exposed by
`/api/meta` for reviewers. Production refuses to boot with DEMO_MODE=true.
"""
from __future__ import annotations

DEMO_PASSWORD = "Praxis@2026#Demo"

DEMO_ACCOUNTS: list[dict[str, str]] = [
    {
        "email": "hod@praxis.demo",
        "role": "HOD",
        "full_name": "Dr. Meera Raghavan",
        "title": "Head of Department",
        "password": DEMO_PASSWORD,
        "mfa": "required",
    },
    {
        "email": "teacher@praxis.demo",
        "role": "TEACHER",
        "full_name": "Prof. Arjun Nair",
        "title": "Database Systems faculty",
        "password": DEMO_PASSWORD,
        "mfa": "optional",
    },
    {
        "email": "student@praxis.demo",
        "role": "STUDENT",
        "full_name": "Riya Sharma",
        "title": "B.Tech CSE, Semester 5",
        "password": DEMO_PASSWORD,
        "mfa": "optional",
    },
]
