"""Create the first administrator without exposing role escalation in registration."""
import getpass
import os
import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

load_dotenv()


def main():
    connection = mysql.connector.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"), port=int(os.getenv("MYSQL_PORT", "3306")),
        database=os.getenv("MYSQL_DATABASE", "derivative_ledger"), user=os.getenv("MYSQL_USER", "ledger"),
        password=os.getenv("MYSQL_PASSWORD", "ledger-local-password"), autocommit=False,
    )
    try:
        name = input("Administrator name: ").strip()
        email = input("Administrator email: ").strip().lower()
        password = getpass.getpass("Password (minimum 10 characters): ")
        if not name or "@" not in email or len(password) < 10:
            raise ValueError("Name, valid email, and 10+ character password are required.")
        cursor = connection.cursor()
        cursor.execute("INSERT INTO users (full_name,email,password_hash,role) VALUES (%s,%s,%s,'ADMINISTRATOR')",
                       (name, email, generate_password_hash(password)))
        user_id = cursor.lastrowid
        cursor.execute("INSERT INTO trader_accounts (user_id,account_name) VALUES (%s,'Primary account')", (user_id,))
        account_id = cursor.lastrowid
        cursor.execute("INSERT INTO margin_accounts (account_id) VALUES (%s)", (account_id,))
        connection.commit()
        print(f"Administrator created with user ID {user_id}.")
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


if __name__ == "__main__":
    main()
