"""
Bug #10: cancel_command and create_command do not write to audit_log.
user.create, user.role_change, satellite.delete all log via log_action().
Command mutations are equally sensitive (they reach the RF link) and
must be audited too — otherwise "who cancelled my pass command?" has no answer.

Fix: add log_action calls in both route handlers.
"""

import ast
import inspect

from src.api.routes.commands import cancel_command, create_command


def _has_log_action_call(func) -> bool:
    source = inspect.getsource(func)
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "log_action":
                return True
            if isinstance(node.func, ast.Attribute) and node.func.attr == "log_action":
                return True
    return False


def test_create_command_writes_audit_log():
    assert _has_log_action_call(create_command), (
        "create_command does not call log_action — command creation must be audited"
    )


def test_cancel_command_writes_audit_log():
    assert _has_log_action_call(cancel_command), (
        "cancel_command does not call log_action — command cancellation must be audited"
    )
