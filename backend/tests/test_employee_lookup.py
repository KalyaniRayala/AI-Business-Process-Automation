from app.services.tool_service import lookup_employee


def test_known_employee_is_found():

    result = lookup_employee("E102")

    assert result["success"] is True
    assert result["employee"] == {
        "employee_id": "E102",
        "name": "Rahul",
        "department": "Finance",
        "manager": "Priya",
        "annual_budget": 75000
    }


def test_unknown_employee_returns_error():

    result = lookup_employee("E999")

    assert result["success"] is False
    assert "E999" in result["error"]
