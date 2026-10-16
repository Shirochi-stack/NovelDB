"""Curated reuse of checked-in GitHub shell steps; never execute marketplace actions."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import source_names


class Failure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def render(text, context):
    def replace(match):
        name = match[1].strip()
        require(name in context, "Unmapped workflow expression: " + name)
        return str(context[name])
    return re.sub(r"\$\{\{(.*?)\}\}", replace, str(text))


def tools(*names):
    for name in names:
        require(shutil.which(name), "Runner prerequisite missing: " + name)


def gate(env, key):
    require(env.get("CI_COMMIT_REF_PROTECTED") == "true" and env.get(key) == "true",
            "This operation requires a protected ref and explicit " + key + "=true.")


class Runner:
    def __init__(self, label, plan=False):
        require(re.fullmatch(r"[a-z0-9-]+", label), "Invalid job label.")
        self.label, self.plan = label, plan
        self.env = dict(os.environ)
        self.env.update(CI="true", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", GITHUB_WORKSPACE=str(ROOT))
        self.context = {"github.workspace": str(ROOT)}
        self.work = ROOT / ".gitlab-work" / label
        self.report = ROOT / "artifacts" / label
        self.operations = []
        if not plan:
            self.work.mkdir(parents=True, exist_ok=True)
            self.report.mkdir(parents=True, exist_ok=True)
            self.env.update(GITHUB_STEP_SUMMARY=str(self.report / "summary.md"), RUNNER_TEMP=str(self.work))

    def command(self, args, cwd=ROOT, label=None, capture=False):
        label = label or str(args[0])
        self.operations.append(label)
        print(label, flush=True)
        if self.plan:
            return ""
        result = subprocess.run([str(arg) for arg in args], cwd=cwd, env=self.env, capture_output=capture, text=True)
        require(result.returncode == 0, f"{label} failed (exit {result.returncode}).")
        return result.stdout if capture else ""

    def shell(self, script, label, shell=None, cwd=ROOT):
        shell = shell or ("pwsh" if sys.platform == "win32" else "bash")
        require(shell in ("bash", "pwsh"), "Unsupported shell.")
        if self.plan:
            self.operations.append(label)
            return
        tools(shell)
        path = self.work / (str(len(self.operations)) + (".ps1" if shell == "pwsh" else ".sh"))
        prefix = "$ErrorActionPreference = 'Stop'\n$PSNativeCommandUseErrorActionPreference = $true\n" if shell == "pwsh" else "set -euo pipefail\n"
        path.write_text(prefix + script, encoding="utf-8", newline="\n")
        args = [shell, "-NoProfile", "-File", path] if shell == "pwsh" else [shell, path]
        self.command(args, cwd=cwd, label=label)

    def step(self, filename, job, name=None, index=None, context=None):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text(encoding="utf-8"))
        steps = workflow["jobs"][job]["steps"]
        matches = [steps[index]] if index is not None else [s for s in steps if s.get("name") == name]
        require(len(matches) == 1 and "run" in matches[0], "Required shell step not found: " + str(name or index))
        step = matches[0]
        ctx = {**self.context, **{"env." + k: v for k, v in self.env.items()}, **(context or {})}
        script = render(step["run"], ctx)
        step_env = {key: render(value, ctx) for key, value in step.get("env", {}).items()}
        cwd = (ROOT / render(step.get("working-directory", "."), ctx)).resolve()
        require(cwd.is_relative_to(ROOT), "Workflow step leaves the checkout.")
        if self.plan:
            self.operations.append(name or "Shell step " + str(index))
            return
        self.env.update(step_env)
        paths = {key: self.work / (str(len(self.operations)) + key) for key in ("GITHUB_ENV", "GITHUB_PATH", "GITHUB_OUTPUT")}
        for key, path in paths.items():
            path.write_text("")
            self.env[key] = str(path)
        self.shell(script, name or "Shell step " + str(index), step.get("shell"), cwd)
        for line in paths["GITHUB_ENV"].read_text(encoding="utf-8-sig").splitlines():
            require("=" in line and "<<" not in line, "Unsupported environment output.")
            key, value = line.split("=", 1)
            self.env[key] = value
        additions = paths["GITHUB_PATH"].read_text(encoding="utf-8-sig").splitlines()
        if additions:
            self.env["PATH"] = os.pathsep.join(additions + [self.env.get("PATH", "")])
        for line in paths["GITHUB_OUTPUT"].read_text(encoding="utf-8-sig").splitlines():
            require("=" in line, "Unsupported step output.")
            key, value = line.split("=", 1)
            if "id" in step:
                self.context[f"steps.{step['id']}.outputs.{key}"] = value

    def collect(self, patterns):
        self.operations.append("Verify expected artifacts")
        if self.plan:
            return
        entries = []
        for pattern in patterns:
            found = sorted(ROOT.glob(pattern))
            require(found, "No artifacts matched required pattern: " + pattern)
            for path in found:
                require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(ROOT), "Artifact is not a regular checkout file.")
                target = self.report / path.name
                require(not target.exists(), "Artifact name collision: " + path.name)
                shutil.copy2(path, target)
                entries.append({"file": path.name, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()})
        (self.report / "manifest.json").write_text(source_names.dumps({"label": self.label, "source_commit": self.env.get("CI_COMMIT_SHA", ""), "artifacts": entries}, indent=2))


def main_guard(function):
    try:
        function()
    except (Failure, OSError, ValueError) as error:
        print("FAILED: " + str(error), file=sys.stderr)
        raise SystemExit(1)
