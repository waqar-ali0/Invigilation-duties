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


def is_blank(value):
    return value is None or (isinstance(value, str) and value == "")
