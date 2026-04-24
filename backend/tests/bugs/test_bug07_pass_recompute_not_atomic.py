"""
Bug #7: _compute_and_store_passes does DELETE then executemany INSERT
outside a transaction. If the INSERT fails halfway, the satellite ends
up with fewer passes than it had before (or none).

Fix: wrap DELETE + INSERT in a single conn.transaction() block.
"""

import ast
import inspect

from src.api.routes.satellites import _compute_and_store_passes


def test_compute_and_store_passes_is_wrapped_in_transaction():
    """The function body must contain `conn.transaction()` — otherwise
    DELETE followed by failing INSERT leaves the satellite with 0 passes."""
    source = inspect.getsource(_compute_and_store_passes)
    tree = ast.parse(source)

    # Look for `async with conn.transaction()` anywhere in the body
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncWith):
            for item in node.items:
                expr = item.context_expr
                if isinstance(expr, ast.Call):
                    attr = expr.func
                    if isinstance(attr, ast.Attribute) and attr.attr == "transaction":
                        found = True
                        break

    assert found, (
        "_compute_and_store_passes must wrap DELETE + executemany INSERT in "
        "`async with conn.transaction():` — otherwise partial failures leave "
        "satellites with missing passes"
    )
