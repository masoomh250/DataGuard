import json

import pytest

from app.rules import validate_rule_configuration


def valid_rules():
    return {
        "dataset": {
            "primary_key": ["customer_id"],
            "check_duplicate_rows": True,
        },
        "columns": {
            "customer_id": {
                "required": True,
                "unique": True,
                "type": "integer",
                "severity": "CRITICAL",
            },
            "email": {
                "required": False,
                "type": "email",
                "severity": "HIGH",
            },
            "age": {
                "required": True,
                "type": "integer",
                "min": 18,
                "max": 100,
            },
        },
    }


def test_valid_configuration_is_normalized():
    result = validate_rule_configuration(
        json.dumps(valid_rules()),
        "customer_id",
    )

    parsed = json.loads(result)

    assert parsed["dataset"]["primary_key"] == ["customer_id"]
    assert parsed["dataset"]["check_duplicate_rows"] is True
    assert parsed["columns"]["customer_id"]["type"] == "integer"
    assert parsed["columns"]["customer_id"]["severity"] == "CRITICAL"


def test_default_primary_key_is_applied():
    rules = {
        "columns": {
            "customer_id": {
                "required": True,
            }
        }
    }

    result = validate_rule_configuration(
        json.dumps(rules),
        "customer_id",
    )

    parsed = json.loads(result)

    assert parsed["dataset"]["primary_key"] == "customer_id"


def test_primary_key_list_is_supported():
    rules = valid_rules()
    rules["dataset"]["primary_key"] = ["customer_id", "email"]

    result = validate_rule_configuration(
        json.dumps(rules),
        "customer_id",
    )

    parsed = json.loads(result)

    assert parsed["dataset"]["primary_key"] == ["customer_id", "email"]


def test_invalid_json_is_rejected():
    with pytest.raises(json.JSONDecodeError):
        validate_rule_configuration(
            "{invalid json",
            "customer_id",
        )


def test_columns_must_be_object():
    rules = {
        "columns": []
    }

    with pytest.raises(ValueError, match="columns"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_invalid_type_is_rejected():
    rules = valid_rules()
    rules["columns"]["age"]["type"] = "currency"

    with pytest.raises(ValueError, match="Unsupported type"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_invalid_severity_is_rejected():
    rules = valid_rules()
    rules["columns"]["age"]["severity"] = "URGENT"

    with pytest.raises(ValueError, match="Invalid severity"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_invalid_range_is_rejected():
    rules = valid_rules()
    rules["columns"]["age"]["min"] = 100
    rules["columns"]["age"]["max"] = 18

    with pytest.raises(ValueError, match="(?i)minimum cannot be greater"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_duplicate_primary_key_values_are_rejected():
    rules = valid_rules()
    rules["dataset"]["primary_key"] = ["customer_id", "customer_id"]

    with pytest.raises(ValueError, match="duplicate"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_invalid_boolean_rule_is_rejected():
    rules = valid_rules()
    rules["columns"]["age"]["required"] = "yes"

    with pytest.raises(ValueError, match="required"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )

def test_primary_key_column_must_exist():
    rules = valid_rules()
    rules["dataset"]["primary_key"] = ["does_not_exist"]

    with pytest.raises(ValueError, match="Primary key column"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_range_requires_numeric_type():
    rules = valid_rules()
    rules["columns"]["email"]["min"] = 1

    with pytest.raises(ValueError, match="Range rules"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )


def test_allowed_values_must_contain_scalar_values():
    rules = valid_rules()
    rules["columns"]["status"] = {
        "allowed_values": [
            "ACTIVE",
            {"invalid": "object"},
        ]
    }

    with pytest.raises(ValueError, match="scalar values"):
        validate_rule_configuration(
            json.dumps(rules),
            "customer_id",
        )
