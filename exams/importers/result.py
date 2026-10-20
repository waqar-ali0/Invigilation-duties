from dataclasses import dataclass, field


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.errors


def missing_columns(rows, required):
    """An ImportResult reporting missing columns, or None when all are present."""
    present = set(rows[0]) if rows else set(required)
    missing = sorted(set(required) - present)
    if missing:
        return ImportResult(errors=[f"Missing column(s): {', '.join(missing)}"])
    return None


def length_errors(row_number, values, limits):
    """Row errors for values longer than their database column allows.

    `limits` maps column name -> (model, field name); checked in that order.
    """
    errors = []
    for column, (model, field) in limits.items():
        max_length = model._meta.get_field(field).max_length
        value = values.get(column)
        if value is not None and not is_blank(value) and len(str(value)) > max_length:
            errors.append(f"Row {row_number}: {column} is longer than {max_length} characters")
    return errors


def is_blank(value):
    return value is None or (isinstance(value, str) and value == "")
