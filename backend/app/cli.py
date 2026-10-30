"""Operator commands that must work without a signed-in HOD.

    python -m app.cli create-user --role HOD --email hod@college.edu --name "Dr. A. Rao"
    python -m app.cli check [--only database|storage|llm]

Creating the first HOD is the only way into a fresh production deployment:
there is no self-registration. The account gets a temporary password (printed
once) that must be changed at first sign-in, and the HOD enrols an
authenticator during that same first sign-in.
"""
from __future__ import annotations

import argparse
import getpass
import sys

from app.core.errors import AppError
from app.db.session import session_scope
from app.models.enums import Role
from app.schemas.users import UserCreate


def _create_user(args: argparse.Namespace) -> int:
    from app.services import user_service

    password: str | None = None
    if args.prompt_password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 2
    try:
        payload = UserCreate(
            email=args.email,
            full_name=args.name,
            role=Role(args.role),
            department=args.department,
            password=password,
        )
        with session_scope() as db:
            user, temporary = user_service.create_user(db, payload, actor=None)
            email, role = user.email, user.role
    except AppError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        return 1
    except ValueError as exc:  # pydantic validation
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Created {role} {email}.")
    if temporary:
        print(f"Temporary password (shown once): {temporary}")
    print("The user must set a new password at first sign-in.")
    if role == Role.HOD:
        print("The HOD will enrol an authenticator app during that first sign-in.")
    return 0


def _check_database() -> None:
    from sqlalchemy import text

    with session_scope() as db:
        db.execute(text("select 1"))


def _check_storage() -> None:
    from app.storage.backend import get_storage

    storage = get_storage()
    key = "healthcheck/praxis-check.bin"
    storage.put(key, b"praxis")
    try:
        if storage.get(key) != b"praxis":
            raise RuntimeError("stored bytes did not read back identically")
    finally:
        storage.delete(key)


def _check_llm() -> None:
    from app.ai.embedder import get_embedder
    from app.ai.provider import build_provider

    context = [
        {
            "content": (
                "A database transaction groups several operations into one unit of work. "
                "Atomicity means either every operation in the transaction takes effect or none does. "
                "Isolation keeps concurrent transactions from seeing each other's partial changes."
            ),
            "document_name": "check",
        }
    ]
    questions = build_provider().generate_practice_questions(
        subject="Databases", topics=["Transactions"], count=2, difficulty="MEDIUM", context=context
    )
    if not questions:
        raise RuntimeError("the model returned no questions")
    vectors = get_embedder().embed(["transaction atomicity", "isolation level"])
    if len(vectors) != 2 or not vectors[0]:
        raise RuntimeError("the embedding call returned no vectors")


def _check(args: argparse.Namespace) -> int:
    """Exercise the external services using the current environment.

    Prints only pass/fail and the error type, never keys or connection strings.
    """
    from app.core.config import settings

    print(f"ENV={settings.ENV} demo_mode={settings.DEMO_MODE} ai_mode={settings.ai_mode} storage={settings.STORAGE_BACKEND}")
    checks = {"database": _check_database, "storage": _check_storage, "llm": _check_llm}
    selected = [args.only] if args.only else list(checks)
    failed = 0
    for name in selected:
        try:
            checks[name]()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {str(exc)[:200]}")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Praxis operator commands")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-user", help="Create an account (use this for the first HOD)")
    create.add_argument("--email", required=True)
    create.add_argument("--name", required=True)
    create.add_argument("--role", choices=[role.value for role in Role], default=Role.HOD.value)
    create.add_argument("--department", default="Computer Science")
    create.add_argument(
        "--prompt-password",
        action="store_true",
        help="Type a password instead of generating a temporary one",
    )
    check = commands.add_parser(
        "check", help="Test the database, file storage and AI provider with the current environment"
    )
    check.add_argument("--only", choices=["database", "storage", "llm"])
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return _create_user(args)
    if args.command == "check":
        return _check(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
