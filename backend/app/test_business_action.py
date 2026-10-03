from app.services.business_action_service import (
    record_expense_reimbursement
)
from app.services.database_service import initialize_database


# The action now writes to the database; make sure the schema exists.
initialize_database()


result = record_expense_reimbursement(
    employee_id="E102",
    employee_name="Rahul",
    amount=750.0,
    department="Finance",
    manager="Priya"
)


print("\n==============================")
print("BUSINESS ACTION TEST")
print("==============================")


print(result)