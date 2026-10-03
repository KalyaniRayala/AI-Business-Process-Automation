import pytest

from app.services.tool_service import lookup_expense_policy


@pytest.mark.parametrize(
    "amount, expected_approval",
    [
        (0, "No approval required."),
        (500, "No approval required."),
        (500.01, "Manager approval required."),
        (750, "Manager approval required."),
        (5000, "Manager approval required."),
        (5000.01, "Manager approval and finance approval required."),
        (6000, "Manager approval and finance approval required."),
    ],
)
def test_policy_thresholds(amount, expected_approval):

    result = lookup_expense_policy(amount)

    assert result == {
        "success": True,
        "expense_amount": amount,
        "approval": expected_approval
    }


def test_negative_amount_is_rejected():

    result = lookup_expense_policy(-1)

    assert result["success"] is False
    assert "negative" in result["error"]
