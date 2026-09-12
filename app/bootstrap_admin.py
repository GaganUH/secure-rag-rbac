import argparse
from getpass import getpass

from app.database import SessionLocal, initialize_database
from app.seed import seed_default_roles
from app.user_service import create_initial_admin


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the first Admin account for Secure RAG with RBAC."
    )
    parser.add_argument("--username", required=True, help="Initial Admin username")
    arguments = parser.parse_args()

    password = getpass("Choose an Admin password: ")
    confirmation = getpass("Enter the password again: ")
    if password != confirmation:
        raise SystemExit("The passwords did not match. No account was created.")

    initialize_database()
    with SessionLocal() as session:
        seed_default_roles(session)
        admin = create_initial_admin(session, arguments.username, password)

    print(f"Initial Admin '{admin.username}' was created successfully.")


if __name__ == "__main__":
    main()

