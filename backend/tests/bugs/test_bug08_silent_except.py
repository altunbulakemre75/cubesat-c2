"""
Bug #8: `_compute_and_store_passes` has a bare `except Exception: continue`
inside the per-station loop. If predict_passes raises (bad TLE, bad station
coords, math domain error), we silently skip that station. Operators see
"no passes for Ankara" and think it's a data gap — actually it's a bug.

Fix: log the exception with enough context (satellite_id, station name,
exception type + message) so it's triageable.
"""

import ast
import inspect

from src.api.routes.satellites import _compute_and_store_passes


def test_except_logs_error_context():
    """The except clause inside per-station loop must log, not silently swallow."""
    source = inspect.getsource(_compute_and_store_passes)
    tree = ast.parse(source)

    # Find all ExceptHandler nodes
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            # Body should contain a Call to a logger, not just a bare `continue`
            body_calls = [n for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
                          if isinstance(n, ast.Call)]
            has_logger_call = any(
                isinstance(c.func, ast.Attribute)
                and c.func.attr in ("exception", "error", "warning")
                for c in body_calls
            )
            assert has_logger_call, (
                "except clause in _compute_and_store_passes swallows errors "
                "without logging — at minimum log.warning(\"...\", exc_info=True)"
            )
