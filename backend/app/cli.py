"""Operator commands that must work without a signed-in HOD.

    python -m app.cli create-user --role HOD --email hod@college.edu --name "Dr. A. Rao"

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
    args = parser.parse_args(argv)
    if args.command == "create-user":
        return _create_user(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
