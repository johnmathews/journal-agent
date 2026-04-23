"""Read / write API pricing configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3


@dataclass
class PricingEntry:
    """A single row from the pricing table."""

    model: str
    category: str  # 'llm' | 'embedding' | 'transcription'
    input_cost_per_mtok: float | None
    output_cost_per_mtok: float | None
    cost_per_minute: float | None
    last_verified: str


def get_all_pricing(conn: sqlite3.Connection) -> list[PricingEntry]:
    """Return every pricing row, ordered by category then model."""
    rows = conn.execute(
        "SELECT model, category, input_cost_per_mtok, output_cost_per_mtok, "
        "cost_per_minute, last_verified FROM pricing ORDER BY category, model",
    ).fetchall()
    return [PricingEntry(**dict(r)) for r in rows]


def update_pricing(
    conn: sqlite3.Connection,
    model: str,
    updates: dict[str, object],
) -> PricingEntry | None:
    """Update pricing for a single model.

    Only cost fields and ``last_verified`` are writable.
    Returns the updated entry, or ``None`` if the model was not found
    or *updates* contained no allowed keys.
    """
    allowed = {"input_cost_per_mtok", "output_cost_per_mtok", "cost_per_minute", "last_verified"}
    filtered = {k: v for k, v in updates.items() if k in allowed}
    if not filtered:
        return None

    # Check model exists
    existing = conn.execute(
        "SELECT model FROM pricing WHERE model = ?", (model,)
    ).fetchone()
    if not existing:
        return None

    set_clause = ", ".join(f"{k} = ?" for k in filtered)
    values = list(filtered.values()) + [model]
    conn.execute(f"UPDATE pricing SET {set_clause} WHERE model = ?", values)  # noqa: S608
    conn.commit()

    row = conn.execute(
        "SELECT model, category, input_cost_per_mtok, output_cost_per_mtok, "
        "cost_per_minute, last_verified FROM pricing WHERE model = ?",
        (model,),
    ).fetchone()
    return PricingEntry(**dict(row)) if row else None
