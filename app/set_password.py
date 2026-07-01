"""Set a user's login password from the command line.

Usage: python -m app.set_password peter.graves@pggi.co.uk

Prompts for the password twice (hidden) and stores its bcrypt hash. This bootstraps
the first admin login; afterwards password changes happen in the app.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.security.passwords import hash_password

_MIN_LENGTH = 8


def main() -> None:
    parser = argparse.ArgumentParser(description="Set a user's login password.")
    parser.add_argument("email", help="Email of the user to set a password for")
    args = parser.parse_args()
    email = args.email.strip().lower()

    session = SessionLocal()
    try:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is None:
            print(f"No user with email {email!r}.")
            sys.exit(1)

        password = getpass.getpass("New password: ")
        if len(password) < _MIN_LENGTH:
            print(f"Password must be at least {_MIN_LENGTH} characters.")
            sys.exit(1)
        if password != getpass.getpass("Confirm password: "):
            print("Passwords do not match.")
            sys.exit(1)

        user.password_hash = hash_password(password)
        session.commit()
        print(f"Password set for {email} (role: {user.role}).")
    finally:
        session.close()


if __name__ == "__main__":
    main()
