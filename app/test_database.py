from app.services.database_service import (
    initialize_database,
    get_employee
)


initialize_database()

employee = get_employee("E102")


print("\n==============================")
print("EMPLOYEE DATABASE TEST")
print("==============================")


if employee:
    print(employee)
else:
    print("Employee not found.")