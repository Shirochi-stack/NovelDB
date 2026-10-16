"""Offline plans and failure checks; no commits, credentials or real builds."""
import importlib.util
import io
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import bridge
import build


class BridgeTests(unittest.TestCase):
    def test_all_supported_plans_resolve_without_execution(self):
        with patch.object(bridge.subprocess, "run", side_effect=AssertionError("Plan executed a process")), \
             patch.object(bridge.Path, "mkdir", side_effect=AssertionError("Plan wrote a directory")):
            for args in build.PLAN_CASES:
                with self.subTest(args=args), redirect_stdout(io.StringIO()):
                    build.main([*args, "--plan"])

    def test_unknown_expression_fails(self):
        with self.assertRaises(bridge.Failure):
            bridge.render("${{ secrets.UNMAPPED }}", {})

    def test_command_failure_propagates(self):
        runner = bridge.Runner("fixture", plan=True)
        runner.plan = False
        with patch.object(bridge.subprocess, "run", return_value=Mock(returncode=5)), redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(bridge.Failure, "exit 5"):
                runner.command(["fixture"], label="Offline failure")

    def test_missing_artifact_fails(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(bridge, "ROOT", Path(temp)):
            runner = bridge.Runner("fixture")
            with self.assertRaisesRegex(bridge.Failure, "No artifacts"):
                runner.collect(["missing/*.zip"])

    def test_sensitive_operation_needs_both_gates(self):
        for env in ({}, {"CI_COMMIT_REF_PROTECTED": "true"}, {"OPERATION_APPROVED": "true"}):
            with self.subTest(env=env), self.assertRaises(bridge.Failure):
                bridge.gate(env, "OPERATION_APPROVED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
