"""
Bug #9: _compute_and_store_passes has an `if not all_passes: return` guard
BEFORE the DELETE statement. So if a new TLE produces zero passes (e.g.
satellite deorbited, or new orbit no longer sees configured stations),
the OLD passes from the previous TLE stay in the DB forever and the UI
shows stale predictions.

Fix: always DELETE first, then INSERT only if there are passes.
"""

import ast
import inspect

from src.api.routes.satellites import _compute_and_store_passes


def test_delete_runs_even_when_no_new_passes():
    """The DELETE statement must execute before any empty-check return.
    Otherwise stale passes from prior TLEs persist."""
    source = inspect.getsource(_compute_and_store_passes)
    tree = ast.parse(source)

    # Find the DELETE execute call and the `if not all_passes: return`.
    # DELETE must come BEFORE the empty-return, not after.
    delete_line = None
    early_return_line = None

    for node in ast.walk(tree):
        # DELETE FROM pass_schedule
        if isinstance(node, ast.Call):
            first_arg = node.args[0] if node.args else None
            if (isinstance(first_arg, ast.Constant)
                    and isinstance(first_arg.value, str)
                    and "DELETE FROM pass_schedule" in first_arg.value):
                delete_line = node.lineno
        # if not all_passes: return
        if isinstance(node, ast.If):
            test = node.test
            if (isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not)
                    and isinstance(test.operand, ast.Name)
                    and test.operand.id == "all_passes"):
                early_return_line = node.lineno

    assert delete_line is not None, "DELETE FROM pass_schedule not found"
    # The bug: early_return happens BEFORE delete. Fix: delete must be first
    # (or early_return must be gone entirely).
    if early_return_line is not None:
        assert delete_line < early_return_line, (
            f"`if not all_passes: return` at line {early_return_line} skips "
            f"the DELETE at line {delete_line} — stale passes never cleared"
        )
