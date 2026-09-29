import sqlite3
from pathlib import Path


DATABASE_PATH = Path("data/business.db")


def get_connection():
    """
    Create a connection to the SQLite database.
    """

    DATABASE_PATH.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    return sqlite3.connect(DATABASE_PATH)


def initialize_database():
    """
    Create the employee table and insert
    initial enterprise data.
    """

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS employees (
            employee_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            manager TEXT NOT NULL,
            annual_budget REAL NOT NULL
        )
    """)

    employees = [
        (
            "E101",
            "Anita",
            "Engineering",
            "Ravi",
            50000
        ),
        (
            "E102",
            "Rahul",
            "Finance",
            "Priya",
            75000
        ),
        (
            "E103",
            "Sneha",
            "Marketing",
            "Arjun",
            40000
        ),
        (
            "E104",
            "Kiran",
            "Engineering",
            "Ravi",
            60000
        )
    ]

    cursor.executemany(
        """
        INSERT OR IGNORE INTO employees
        (
            employee_id,
            name,
            department,
            manager,
            annual_budget
        )
        VALUES (?, ?, ?, ?, ?)
        """,
        employees
    )

    connection.commit()
    connection.close()


def get_employee(employee_id: str):
    """
    Retrieve an employee using employee ID.
    """

    connection = get_connection()
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT
            employee_id,
            name,
            department,
            manager,
            annual_budget
        FROM employees
        WHERE employee_id = ?
        """,
        (employee_id,)
    )

    row = cursor.fetchone()

    connection.close()

    if row is None:
        return None

    return {
        "employee_id": row[0],
        "name": row[1],
        "department": row[2],
        "manager": row[3],
        "annual_budget": row[4]
    }