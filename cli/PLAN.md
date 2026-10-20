# `felis` CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `cli/felis`, a single-script Python CLI that generates QIR from Qiskit circuits and runs and inspects jobs on Alice & Bob's Felis API.

**Architecture:** There is one executable Python file (`cli/felis`, no `.py` extension):
- It is organised in sections: config, client, circuits, jobs, commands, and the argparse CLI.
- It reuses `qiskit-alice-bob-provider`'s `ApiClient`, its job/target API functions, and its transpile pipeline.
- Provider and Qiskit imports are done lazily inside functions, so `--help` is fast and needs no API key.

Tests load the script with `importlib` and swap the API client for an in-memory fake.

**Tech Stack:** Python 3.11/3.12, `qiskit-alice-bob-provider` ≥ 3.0 (brings `qiskit`, `requests`), `python-dotenv`, `pytest`.

**Spec:** `cli/DESIGN.md`

## Global Constraints

- One executable script at `cli/felis`, starting with `#!/usr/bin/env python3`. There is no package and no `setup.py`.
- Environment variables are exactly `QRMI_AB_FELIS_API_KEY` and `QRMI_AB_FELIS_BASE_ENDPOINT`. The default endpoint is exactly `https://api.alice-bob.com/`.
- Precedence: CLI flag beats the environment, which beats `./.env` (current directory), which beats the default.
- `--help`, argument errors and local file errors must never need an API key. Do not import `qiskit` or `qiskit_alice_bob_provider` at module level.
- Data (QIR, JSON, counts) goes to stdout. Progress, events and messages go to stderr.
- Exit codes:
  - 0 for success;
  - 1 for a usage, config or API error;
  - 2 when a job ended unsuccessfully;
  - 3 when the local `--timeout` expires;
  - 130 when interrupted.
- API errors print as one line: `felis: HTTP <code>: <message>`. `--debug` adds the traceback.
- Counts keys are the API's bitstrings as-is (e.g. `"0"`), not hex.
- Don't generate commands from the OpenAPI spec, and don't add a raw-request command.
- Git: commit only if the user has asked for commits. Never add `Co-Authored-By` or any AI attribution to commit messages.

## Review Focus

1. **A circuit file that raises or has a syntax error** should give a one-line `felis: <ExceptionType>: <message>` with exit code 1, not a raw traceback (Task 2, `test_generate_reports_error_in_circuit_file`).
2. **An unknown target name** in `generate`, or in `run` with a `.py` input, should name the available targets (Task 2, `test_find_target_unknown_lists_available`).
3. **A mistyped job ID** (not a UUID): the API's 400 `InvalidProperty` body should become a readable one-line message (Task 1, `test_describe_api_error_invalid_job_id`).
4. **`run` with a missing `.ll` file or a wrong extension** should fail with a clear message before asking for an API key (Task 4, `test_run_bad_input_fails_before_needing_key`).
5. **Ctrl-C while waiting with non-interactive stdin** (CI, a pod) shouldn't prompt or hang. The job should be left running, its ID printed, and the exit code should be 130 (Task 4, `test_run_interrupt_non_interactive_leaves_job`).

---

## File Structure

| File | Responsibility |
|---|---|
| `cli/felis` | The CLI (all code) |
| `cli/circuit.py` | Example circuit: a top-level `circuit` |
| `cli/requirements.txt` | Runtime dependencies |
| `cli/requirements-dev.txt` | Test dependencies |
| `cli/test_felis.py` | pytest suite |
| `cli/testdata/targets.json` | Captured `EMU:1Q:LESCANNE_2020` target description, for offline transpile tests |
| `cli/testdata/expected_lescanne.ll` | Expected QIR for `circuit.py` on that target |
| `cli/README.md` | Install and usage |
| `hands-on/module-2/alice-bob/README.md` | Modify: use `felis generate` |
| `hands-on/module-2/alice-bob/generate_input.py` | Delete |

The repo root is `/Users/jmachin/ab/src/external/demo-qrmi-k8s`. All commands below run from the repo root unless they `cd`.

---

### Task 1: Scaffold, config, parameters, errors and CLI entry point

**Files:**
- Create: `cli/felis`, `cli/requirements.txt`, `cli/requirements-dev.txt`, `cli/test_felis.py`

**Interfaces:**
- Produces:
  - Errors:
    - `FelisError(Exception)` with class attribute `exit_code = 1`.
    - `JobUnsuccessful(FelisError)` with `exit_code = 2`.
    - `WaitTimeout(FelisError)` with `exit_code = 3`.
  - Output helpers: `eprint(*args, **kwargs)`, `print_json(value)`, `print_text(text: str)`.
  - Config:
    - `Config(api_key: str, endpoint: str)` (a dataclass).
    - `load_config(args) -> Config`.
    - Constants `API_KEY_VAR`, `ENDPOINT_VAR`, `DEFAULT_ENDPOINT`.
  - Client:
    - `make_client(config: Config) -> ApiClient`.
    - `client_for(args) -> ApiClient`. Commands call this, and tests monkeypatch it.
  - `parse_params(pairs: list[str] | None, shots: int | None = None) -> dict`.
  - `describe_api_error(error) -> str`.
  - CLI:
    - `build_parser() -> argparse.ArgumentParser`.
    - `main(argv: list[str] | None = None) -> int`.
    - `COMMAND_GROUPS`: a tuple of functions `add_x_commands(commands)`, each taking the argparse subparsers object. It's empty in this task; later tasks extend it.
  - Command contract: a command is registered with `parser.set_defaults(func=cmd_x)`. `cmd_x(args)` returns `None` (exit 0) or raises.
  - A parser with sub-commands sets `help_parser=<itself>`, so a bare `felis api` prints that parser's help.
  - Test helpers in `test_felis.py`: `felis` (the loaded module), `FakeClient`, `FakeResponse`, `api_error(status, body)`, and the fixtures `fake_client`, `fake_time`, plus `clean_env` (autouse).

- [ ] **Step 1: Create dependency files and a venv**

`cli/requirements.txt`:
```
python-dotenv
qiskit-alice-bob-provider>=3.0
```

`cli/requirements-dev.txt`:
```
-r requirements.txt
pytest
```

Run:
```bash
hands-on/module-2/alice-bob/.venv/bin/python -m venv cli/.venv
cli/.venv/bin/pip install -r cli/requirements-dev.txt
```
Expected: the install succeeds, and `cli/.venv/bin/pytest --version` prints a version. `.venv/` is already in the root `.gitignore`.

- [ ] **Step 2: Write the failing tests**

Create `cli/test_felis.py`:
```python
"""Tests for the felis CLI. Run with: cd cli && .venv/bin/pytest -v"""

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import requests

HERE = Path(__file__).parent
FELIS = HERE / "felis"
JOB_ID = "0b4f6d2e-4a8e-4c43-9a38-2f1c5d9e7a10"


def _load_felis():
    loader = importlib.machinery.SourceFileLoader("felis", str(FELIS))
    spec = importlib.util.spec_from_loader("felis", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


felis = _load_felis()


class FakeResponse:
    """Stands in for requests.Response. bytes bodies are raw, anything else is JSON."""

    def __init__(self, body):
        self.content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.text = self.content.decode()

    def json(self):
        return json.loads(self.content)


class FakeClient:
    """Stands in for the provider's ApiClient.

    routes maps (METHOD, endpoint) to a body, an exception to raise, or a
    zero-argument callable returning either. Unknown routes return {}.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def _request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        body = self.routes.get((method, endpoint), {})
        if callable(body):
            body = body()
        if isinstance(body, Exception):
            raise body
        return FakeResponse(body)

    def get(self, endpoint, **kwargs):
        return self._request("GET", endpoint, **kwargs)

    def post(self, endpoint, **kwargs):
        return self._request("POST", endpoint, **kwargs)

    def delete(self, endpoint, **kwargs):
        return self._request("DELETE", endpoint, **kwargs)


def api_error(status, body):
    from qiskit_alice_bob_provider.remote.api.client import AliceBobApiException

    response = requests.Response()
    response.status_code = status
    response._content = body.encode()  # pylint: disable=protected-access
    return AliceBobApiException(response)


class FakeTime:
    """Replaces the `time` module inside felis: sleeping advances the clock instantly."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


@pytest.fixture(autouse=True)
def clean_env(tmp_path, monkeypatch):
    monkeypatch.delenv(felis.API_KEY_VAR, raising=False)
    monkeypatch.delenv(felis.ENDPOINT_VAR, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def fake_time(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(felis, "time", fake)
    return fake


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(felis, "client_for", lambda args: client)
    return client


def parse(argv):
    return felis.build_parser().parse_args(argv)


# ---------------------------------------------------------------- config


def test_config_flag_beats_env_and_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("QRMI_AB_FELIS_API_KEY=from-dotenv\n")
    monkeypatch.setenv(felis.API_KEY_VAR, "from-env")
    assert felis.load_config(parse(["--api-key", "from-flag"])).api_key == "from-flag"


def test_config_env_beats_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("QRMI_AB_FELIS_API_KEY=from-dotenv\n")
    monkeypatch.setenv(felis.API_KEY_VAR, "from-env")
    assert felis.load_config(parse([])).api_key == "from-env"


def test_config_reads_dotenv_in_current_directory(tmp_path):
    (tmp_path / ".env").write_text(
        "QRMI_AB_FELIS_API_KEY=from-dotenv\n"
        "QRMI_AB_FELIS_BASE_ENDPOINT=https://example.test/\n"
    )
    config = felis.load_config(parse([]))
    assert config == felis.Config(api_key="from-dotenv", endpoint="https://example.test/")


def test_config_default_endpoint(monkeypatch):
    monkeypatch.setenv(felis.API_KEY_VAR, "key")
    assert felis.load_config(parse([])).endpoint == "https://api.alice-bob.com/"


def test_config_missing_key_names_variable():
    with pytest.raises(felis.FelisError, match="QRMI_AB_FELIS_API_KEY"):
        felis.load_config(parse([]))


# ---------------------------------------------------------------- parameters


@pytest.mark.parametrize(
    "pairs, expected",
    [
        (["nbShots=100"], {"nbShots": 100}),
        (["averageNbPhotons=4.5"], {"averageNbPhotons": 4.5}),
        (["memory=true"], {"memory": True}),
        (["name=hello"], {"name": "hello"}),
        (["expr=a=b"], {"expr": "a=b"}),
        (["empty="], {"empty": ""}),
        (None, {}),
    ],
)
def test_parse_params(pairs, expected):
    assert felis.parse_params(pairs) == expected


def test_parse_params_shots_overrides_nbshots():
    assert felis.parse_params(["nbShots=1"], shots=7) == {"nbShots": 7}


@pytest.mark.parametrize("bad", ["noequals", "=value"])
def test_parse_params_rejects_malformed(bad):
    with pytest.raises(felis.FelisError, match="KEY=VALUE"):
        felis.parse_params([bad])


# ---------------------------------------------------------------- errors and entry point


def test_describe_api_error_uses_error_message():
    error = api_error(404, '{"error":{"code":404,"message":"Could not find target NOPE."}}')
    assert felis.describe_api_error(error) == "HTTP 404: Could not find target NOPE."


def test_describe_api_error_invalid_job_id():  # Review Focus 3
    body = (
        '{"error":{"code":"InvalidProperty","message":"Field path->job_id: '
        'Input should be a valid UUID"}}'
    )
    assert felis.describe_api_error(api_error(400, body)) == (
        "HTTP 400: Field path->job_id: Input should be a valid UUID"
    )


def test_describe_api_error_uses_detail():
    error = api_error(401, '{"detail":"Not authenticated"}')
    assert felis.describe_api_error(error) == "HTTP 401: Not authenticated"


def test_describe_api_error_falls_back_to_body():
    assert felis.describe_api_error(api_error(502, "Bad Gateway")) == "HTTP 502: Bad Gateway"


def test_no_command_prints_help_and_fails(capsys):
    assert felis.main([]) == 1
    assert "usage: felis" in capsys.readouterr().err


def test_help_runs_as_a_script_without_api_key():
    result = subprocess.run(
        [sys.executable, str(FELIS), "--help"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0
    assert "usage: felis" in result.stdout
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd cli && .venv/bin/pytest -v`
Expected: collection fails with `FileNotFoundError` for `felis`.

- [ ] **Step 4: Write the minimal implementation**

Create `cli/felis`:
```python
#!/usr/bin/env python3
"""felis: a command-line client for Alice & Bob's Felis API.

Run `felis --help` for usage. See DESIGN.md for the design.
"""

# This code is part of Qiskit.
#
# (C) Copyright 2026 Alice and Bob. All Rights Reserved.
#
# This code is licensed under the Apache License, Version 2.0. You may
# obtain a copy of this license in the LICENSE.txt file in the root directory
# of this source tree or at http://www.apache.org/licenses/LICENSE-2.0.
#
# Any modifications or derivative works of this code must retain this
# copyright notice, and modified files need to carry a notice indicating
# that they have been altered from the originals.

# Qiskit and the provider are imported inside functions, so that --help and
# argument errors are fast and never need an API key.

import argparse
import csv
import io
import json
import os
import runpy
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

API_KEY_VAR = "QRMI_AB_FELIS_API_KEY"
ENDPOINT_VAR = "QRMI_AB_FELIS_BASE_ENDPOINT"
DEFAULT_ENDPOINT = "https://api.alice-bob.com/"


# ---------------------------------------------------------------- errors and output


class FelisError(Exception):
    """An error shown to the user as a single `felis: ...` line."""

    exit_code = 1


class JobUnsuccessful(FelisError):
    """The job ended in a failed, cancelled or timed-out state."""

    exit_code = 2


class WaitTimeout(FelisError):
    """The local --timeout expired while the job was still running."""

    exit_code = 3


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def print_json(value):
    print(json.dumps(value, indent=2))


def print_text(text):
    sys.stdout.write(text if text.endswith("\n") else text + "\n")


def describe_api_error(error):
    """Turn an AliceBobApiException into `HTTP <code>: <message>`."""
    body = str(error).strip()
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    message = None
    if isinstance(data, dict):
        inner = data.get("error")
        if isinstance(inner, dict):
            message = inner.get("message")
        message = message or data.get("detail")
    if message is not None and not isinstance(message, str):
        message = json.dumps(message)
    return f"HTTP {error.status_code}: {message or body}"


# ---------------------------------------------------------------- config and client


@dataclass
class Config:
    api_key: str
    endpoint: str


def load_config(args):
    """Resolve settings: flag, then environment, then ./.env, then default."""
    from dotenv import dotenv_values

    dotenv = dotenv_values(Path.cwd() / ".env")

    def setting(flag, var):
        return flag or os.environ.get(var) or dotenv.get(var)

    api_key = setting(args.api_key, API_KEY_VAR)
    if not api_key:
        raise FelisError(
            f"no API key: set {API_KEY_VAR} (in the environment or ./.env) or pass --api-key"
        )
    endpoint = setting(args.endpoint, ENDPOINT_VAR) or DEFAULT_ENDPOINT
    return Config(api_key=api_key, endpoint=endpoint)


def make_client(config):
    from qiskit_alice_bob_provider.remote.api.client import ApiClient

    return ApiClient(api_key=config.api_key, url=config.endpoint)


def client_for(args):
    """Build an API client from the command line and environment."""
    return make_client(load_config(args))


# ---------------------------------------------------------------- parameters


def parse_params(pairs, shots=None):
    """Turn KEY=VALUE pairs into inputParams; values are JSON where they parse."""
    params = {}
    for pair in pairs or []:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise FelisError(f"invalid --param {pair!r}: expected KEY=VALUE")
        try:
            params[key] = json.loads(value)
        except ValueError:
            params[key] = value
    if shots is not None:
        params["nbShots"] = shots
    return params


# ---------------------------------------------------------------- CLI


def build_parser():
    parser = argparse.ArgumentParser(
        prog="felis", description="Command-line client for Alice & Bob's Felis API."
    )
    parser.add_argument("--api-key", help=f"API key (default: ${API_KEY_VAR})")
    parser.add_argument(
        "--endpoint", help=f"API base URL (default: ${ENDPOINT_VAR} or {DEFAULT_ENDPOINT})"
    )
    parser.add_argument("--debug", action="store_true", help="show full tracebacks")
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")
    for add_commands in COMMAND_GROUPS:
        add_commands(commands)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        getattr(args, "help_parser", parser).print_help(sys.stderr)
        return 1
    try:
        args.func(args)
        return 0
    except KeyboardInterrupt:
        eprint("felis: interrupted")
        return 130
    except FelisError as error:
        if args.debug:
            traceback.print_exc()
        eprint(f"felis: {error}")
        return error.exit_code
    except Exception as error:  # pylint: disable=broad-except
        from qiskit_alice_bob_provider.remote.api.client import AliceBobApiException

        if args.debug:
            traceback.print_exc()
        if isinstance(error, AliceBobApiException):
            eprint(f"felis: {describe_api_error(error)}")
        else:
            eprint(f"felis: {type(error).__name__}: {error}")
        return 1


COMMAND_GROUPS = ()

if __name__ == "__main__":
    sys.exit(main())
```

Then run: `chmod +x cli/felis`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS.

- [ ] **Step 6: Checkpoint**

If the user has asked for commits:
```bash
git add cli/felis cli/requirements.txt cli/requirements-dev.txt cli/test_felis.py
git commit -m "cli: add felis skeleton with config, params and error handling"
```

---

### Task 2: Circuits and `felis generate`

**Files:**
- Modify: `cli/felis`: add a `# ---- circuits` section after the parameters section, and change `COMMAND_GROUPS`.
- Create: `cli/circuit.py`, `cli/testdata/targets.json`, `cli/testdata/expected_lescanne.ll`
- Test: `cli/test_felis.py` (append)

**Interfaces:**
- Consumes (from Task 1): `FelisError`, `client_for`, `print_text`, `COMMAND_GROUPS`.
- Produces:
  - `load_circuit(path) -> QuantumCircuit`.
  - `find_target(client, name: str) -> dict`.
  - `to_qir(circuit, target_name: str, client) -> str`.
  - `check_input(path) -> Path`: raises unless the path is an existing `.ll` or `.py` file.
  - `read_input(path, target_name: str, client) -> str`: returns QIR for a `.ll` or `.py` input.
  - `cmd_generate(args)` and `add_circuit_commands(commands)`.

- [ ] **Step 1: Capture the test data**

The `.env` in module-2 holds a working key. This step reads it without printing it:
```bash
mkdir -p cli/testdata
cp hands-on/module-2/alice-bob/generated_circuit.ll cli/testdata/expected_lescanne.ll
cd cli && .venv/bin/python - <<'EOF'
import json
from dotenv import dotenv_values
from qiskit_alice_bob_provider.remote.api.client import ApiClient
from qiskit_alice_bob_provider.remote.api.targets import list_targets

env = dotenv_values("../hands-on/module-2/alice-bob/.env")
client = ApiClient(env["QRMI_AB_FELIS_API_KEY"], env["QRMI_AB_FELIS_BASE_ENDPOINT"])
targets = [t for t in list_targets(client) if t["name"] == "EMU:1Q:LESCANNE_2020"]
assert len(targets) == 1
with open("testdata/targets.json", "w") as f:
    json.dump(targets, f, indent=2)
EOF
```
Expected: `cli/testdata/targets.json` holds a one-element list whose `name` is `EMU:1Q:LESCANNE_2020`.

- [ ] **Step 2: Create the example circuit**

`cli/circuit.py`:
```python
"""Example circuit for `felis`: prepare |+> and measure in the X basis."""

from qiskit import QuantumCircuit

circuit = QuantumCircuit(1, 1)
circuit.initialize("+")
circuit.measure_x(0, 0)  # pylint: disable=no-member
```

- [ ] **Step 3: Write the failing tests**

Append to `cli/test_felis.py`:
```python
# ---------------------------------------------------------------- circuits

TARGETS = json.loads((HERE / "testdata" / "targets.json").read_text())
LESCANNE = "EMU:1Q:LESCANNE_2020"


def test_load_circuit_example():
    from qiskit import QuantumCircuit

    assert isinstance(felis.load_circuit(HERE / "circuit.py"), QuantumCircuit)


def test_load_circuit_missing_variable(tmp_path):
    path = tmp_path / "empty.py"
    path.write_text("x = 1\n")
    with pytest.raises(felis.FelisError, match=r"empty\.py: must define .*`circuit`"):
        felis.load_circuit(path)


def test_load_circuit_wrong_type(tmp_path):
    path = tmp_path / "wrong.py"
    path.write_text("circuit = 'not a circuit'\n")
    with pytest.raises(felis.FelisError, match="QuantumCircuit"):
        felis.load_circuit(path)


def test_load_circuit_missing_file(tmp_path):
    with pytest.raises(felis.FelisError, match="no such file"):
        felis.load_circuit(tmp_path / "nope.py")


def test_generate_reports_error_in_circuit_file(tmp_path, capsys):  # Review Focus 1
    path = tmp_path / "broken.py"
    path.write_text("raise ValueError('oops')\n")
    assert felis.main(["generate", LESCANNE, str(path)]) == 1
    assert capsys.readouterr().err == "felis: ValueError: oops\n"


def test_find_target_unknown_lists_available():  # Review Focus 2
    client = FakeClient({("GET", "v1/targets/"): [{"name": "A"}, {"name": "B"}]})
    with pytest.raises(felis.FelisError, match="unknown target 'C'; available targets: A, B"):
        felis.find_target(client, "C")


def test_to_qir_matches_expected_output():
    client = FakeClient({("GET", "v1/targets/"): TARGETS})
    qir = felis.to_qir(felis.load_circuit(HERE / "circuit.py"), LESCANNE, client)
    expected = (HERE / "testdata" / "expected_lescanne.ll").read_text()
    assert qir.rstrip("\n") == expected.rstrip("\n")


def test_generate_writes_output_file(tmp_path, fake_client):
    fake_client.routes[("GET", "v1/targets/")] = TARGETS
    out = tmp_path / "out.ll"
    assert felis.main(["generate", LESCANNE, str(HERE / "circuit.py"), "-o", str(out)]) == 0
    assert out.read_text().rstrip("\n") == (
        (HERE / "testdata" / "expected_lescanne.ll").read_text().rstrip("\n")
    )


def test_read_input_ll_is_passed_through(tmp_path):
    path = tmp_path / "c.ll"
    path.write_text("QIR TEXT")
    assert felis.read_input(path, LESCANNE, client=None) == "QIR TEXT"


@pytest.mark.parametrize("name", ["c.txt", "missing.ll"])
def test_check_input_rejects(tmp_path, name):
    (tmp_path / "c.txt").write_text("x")
    with pytest.raises(felis.FelisError):
        felis.check_input(tmp_path / name)
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `cd cli && .venv/bin/pytest -v -k "circuit or target or qir or generate or input"`
Expected: FAIL with `AttributeError: module 'felis' has no attribute 'load_circuit'`. The generate tests fail with an argparse `invalid choice` error.

- [ ] **Step 5: Write the minimal implementation**

In `cli/felis`, insert this section before `# ---------------------------------------------------------------- CLI`:
```python
# ---------------------------------------------------------------- circuits


def load_circuit(path):
    """Run a Python file and return its top-level `circuit`."""
    from qiskit import QuantumCircuit

    path = Path(path)
    if not path.is_file():
        raise FelisError(f"{path}: no such file")
    circuit = runpy.run_path(str(path)).get("circuit")
    if not isinstance(circuit, QuantumCircuit):
        raise FelisError(
            f"{path}: must define a top-level variable `circuit` of type QuantumCircuit"
        )
    return circuit


def find_target(client, name):
    from qiskit_alice_bob_provider.remote.api.targets import list_targets

    targets = list_targets(client)
    for target in targets:
        if target["name"] == name:
            return target
    available = ", ".join(sorted(t["name"] for t in targets))
    raise FelisError(f"unknown target {name!r}; available targets: {available}")


def to_qir(circuit, target_name, client):
    """Transpile a circuit for a Felis target and return it as QIR."""
    from qiskit import transpile
    from qiskit.transpiler import PassManager
    from qiskit_alice_bob_provider.plugins.state_preparation import EnsurePreparationPass
    from qiskit_alice_bob_provider.qir import circuit_to_qir
    from qiskit_alice_bob_provider.remote.backend import AliceBobRemoteBackend

    # The backend is only used for transpilation; nothing is submitted through it.
    backend = AliceBobRemoteBackend(client, find_target(client, target_name))
    transpiled = transpile(circuit, backend)
    # As in AliceBobRemoteBackend.run(), run a final EnsurePreparationPass
    # after the preset pass manager has run.
    # This works around https://github.com/Qiskit/qiskit/issues/6943
    final = PassManager([EnsurePreparationPass()]).run(transpiled)
    return circuit_to_qir(final)


def check_input(path):
    """Check INPUT is an existing .ll or .py file, before any API call."""
    path = Path(path)
    if path.suffix not in (".ll", ".py"):
        raise FelisError(f"{path}: expected a .ll (QIR) or .py (circuit) file")
    if not path.is_file():
        raise FelisError(f"{path}: no such file")
    return path


def read_input(path, target_name, client):
    """Return QIR for INPUT: a .ll file as-is, or a .py circuit file transpiled."""
    path = check_input(path)
    if path.suffix == ".py":
        return to_qir(load_circuit(path), target_name, client)
    return path.read_text()


def cmd_generate(args):
    circuit = load_circuit(args.circuit)
    qir = to_qir(circuit, args.target, client_for(args))
    if args.output:
        Path(args.output).write_text(qir)
    else:
        print_text(qir)


def add_circuit_commands(commands):
    parser = commands.add_parser("generate", help="transpile a Python circuit file to QIR")
    parser.add_argument("target", help="Felis target, e.g. EMU:1Q:LESCANNE_2020")
    parser.add_argument("circuit", help="Python file defining a top-level `circuit`")
    parser.add_argument("-o", "--output", help="write QIR to this file instead of stdout")
    parser.set_defaults(func=cmd_generate)
```

Change `COMMAND_GROUPS = ()` to:
```python
COMMAND_GROUPS = (add_circuit_commands,)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS. In `test_generate_reports_error_in_circuit_file`, the circuit file is loaded before `client_for`, so no key is needed.

- [ ] **Step 7: Checkpoint**

If the user has asked for commits:
```bash
git add cli/felis cli/circuit.py cli/testdata cli/test_felis.py
git commit -m "cli: add felis generate"
```

---

### Task 3: Job primitives (submit, wait, counts)

**Files:**
- Modify: `cli/felis`: add a `# ---- jobs` section after the circuits section.
- Test: `cli/test_felis.py` (append)

**Interfaces:**
- Consumes: `FelisError`, `JobUnsuccessful`, `WaitTimeout`, `eprint`, `print_json`.
- Produces:
  - Constants: `TERMINAL_EVENTS: frozenset[str]`, `POLL_INTERVAL = 1.0`.
  - Job state:
    - `latest_event(job: dict) -> str | None`.
    - `submit(client, target: str, qir: str, params: dict) -> dict`: returns the created job.
    - `wait(client, job_id: str, timeout: float | None = None, on_event=None) -> dict`:
      - returns the job once its state is terminal;
      - raises `WaitTimeout`;
      - uses the module-level `time.monotonic` and `time.sleep`, which tests replace through the `fake_time` fixture.
    - `check_succeeded(job: dict) -> None`: raises `JobUnsuccessful`.
  - Formatting:
    - `describe_job_error(error) -> str`.
    - `format_event(event: dict) -> str`.
  - Counts:
    - `parse_counts(output: str) -> dict[str, int]`.
    - `counts(client, job_id: str) -> dict[str, int]`.
    - `print_counts(job_id: str, result: dict, as_json: bool) -> None`.

- [ ] **Step 1: Write the failing tests**

Append to `cli/test_felis.py`:
```python
# ---------------------------------------------------------------- jobs


def job_with(*types, errors=()):
    events = [{"type": t, "createdAt": f"2026-10-20T10:00:0{i}"} for i, t in enumerate(types)]
    return {"id": JOB_ID, "target": "T", "events": events, "errors": list(errors)}


def sequence(*bodies):
    """A route body that returns each item in turn, then repeats the last."""
    remaining = list(bodies)
    return lambda: remaining.pop(0) if len(remaining) > 1 else remaining[0]


def test_submit_creates_job_then_uploads_input():
    client = FakeClient({("POST", "v1/jobs/"): {"id": JOB_ID}})
    job = felis.submit(client, "T", "QIR", {"nbShots": 5})
    assert job == {"id": JOB_ID}
    assert client.calls == [
        (
            "POST",
            "v1/jobs/",
            {
                "json": {
                    "inputDataFormat": "HUMAN_QIR",
                    "outputDataFormat": "HISTOGRAM",
                    "target": "T",
                    "inputParams": {"nbShots": 5},
                }
            },
        ),
        ("POST", f"v1/jobs/{JOB_ID}/input", {"files": {"input": "QIR"}}),
    ]


def test_wait_reports_each_event_once():
    client = FakeClient(
        {
            ("GET", f"v1/jobs/{JOB_ID}"): sequence(
                job_with(),
                job_with("CREATED"),
                job_with("CREATED", "INPUT_READY"),
                job_with("CREATED", "INPUT_READY", "EXECUTING", "SUCCEEDED"),
            )
        }
    )
    seen = []
    job = felis.wait(client, JOB_ID, on_event=lambda e: seen.append(e["type"]))
    assert seen == ["CREATED", "INPUT_READY", "EXECUTING", "SUCCEEDED"]
    assert felis.latest_event(job) == "SUCCEEDED"


@pytest.mark.parametrize(
    "state",
    ["COMPILATION_FAILED", "TRANSPILATION_FAILED", "EXECUTION_FAILED", "CANCELLED", "TIMED_OUT"],
)
def test_wait_stops_on_unsuccessful_states(state):
    client = FakeClient({("GET", f"v1/jobs/{JOB_ID}"): job_with("CREATED", state)})
    job = felis.wait(client, JOB_ID)
    with pytest.raises(felis.JobUnsuccessful, match=f"ended with {state}") as raised:
        felis.check_succeeded(job)
    assert raised.value.exit_code == 2


def test_wait_times_out(fake_time):
    client = FakeClient({("GET", f"v1/jobs/{JOB_ID}"): job_with("CREATED", "INPUT_READY")})
    with pytest.raises(felis.WaitTimeout, match=f"{JOB_ID} is still running") as raised:
        felis.wait(client, JOB_ID, timeout=5)
    assert raised.value.exit_code == 3
    assert fake_time.now >= 5


def test_check_succeeded_includes_job_errors():
    job = job_with("CREATED", "COMPILATION_FAILED", errors=[{"message": "bad QIR"}, "plain"])
    with pytest.raises(felis.JobUnsuccessful, match="bad QIR; plain"):
        felis.check_succeeded(job)


def test_check_succeeded_passes_on_success():
    felis.check_succeeded(job_with("CREATED", "SUCCEEDED"))


def test_parse_counts():
    assert felis.parse_counts("0,49\n1,1\n") == {"0": 49, "1": 1}
    assert felis.parse_counts("") == {}


def test_counts_not_available_yet():
    client = FakeClient({("GET", f"v1/jobs/{JOB_ID}/output"): api_error(409, "not ready")})
    with pytest.raises(felis.FelisError, match="no output"):
        felis.counts(client, JOB_ID)


def test_print_counts_text(capsys):
    felis.print_counts(JOB_ID, {"1": 1, "0": 49}, as_json=False)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["0", "49", "98.0%"]
    assert lines[1].split() == ["1", "1", "2.0%"]


def test_print_counts_json(capsys):
    felis.print_counts(JOB_ID, {"0": 50}, as_json=True)
    assert json.loads(capsys.readouterr().out) == {"id": JOB_ID, "counts": {"0": 50}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd cli && .venv/bin/pytest -v -k "submit or wait or succeeded or counts"`
Expected: FAIL with `AttributeError: module 'felis' has no attribute 'submit'` (and similar for the others).

- [ ] **Step 3: Write the minimal implementation**

In `cli/felis`, insert this section before `# ---------------------------------------------------------------- CLI`:
```python
# ---------------------------------------------------------------- jobs

TERMINAL_EVENTS = frozenset(
    {
        "SUCCEEDED",
        "COMPILATION_FAILED",
        "TRANSPILATION_FAILED",
        "EXECUTION_FAILED",
        "CANCELLED",
        "TIMED_OUT",
    }
)
POLL_INTERVAL = 1.0


def latest_event(job):
    events = job.get("events") or []
    return events[-1]["type"] if events else None


def submit(client, target, qir, params):
    """Create a job and upload its QIR input; return the created job."""
    from qiskit_alice_bob_provider.remote.api import jobs

    job = jobs.create_job(client, target, params)
    jobs.upload_input(client, job["id"], qir)
    return job


def wait(client, job_id, timeout=None, on_event=None):
    """Poll a job until it reaches a terminal state, reporting each new event."""
    from qiskit_alice_bob_provider.remote.api.jobs import get_job

    start = time.monotonic()
    seen = 0
    while True:
        job = get_job(client, job_id)
        events = job.get("events") or []
        if on_event:
            for event in events[seen:]:
                on_event(event)
        seen = len(events)
        if latest_event(job) in TERMINAL_EVENTS:
            return job
        if timeout is not None and time.monotonic() - start >= timeout:
            raise WaitTimeout(f"timed out after {timeout:g}s; job {job_id} is still running")
        time.sleep(POLL_INTERVAL)


def describe_job_error(error):
    if isinstance(error, dict):
        return error.get("message") or json.dumps(error)
    return str(error)


def check_succeeded(job):
    state = latest_event(job)
    if state == "SUCCEEDED":
        return
    message = f"job {job['id']} ended with {state}"
    errors = "; ".join(describe_job_error(e) for e in job.get("errors") or [])
    raise JobUnsuccessful(f"{message}: {errors}" if errors else message)


def format_event(event):
    return f"  {event.get('createdAt', '')}  {event['type']}"


def parse_counts(output):
    """Parse the API's histogram CSV (`<bitstring>,<count>` rows)."""
    return {row[0]: int(row[1]) for row in csv.reader(io.StringIO(output)) if row}


def counts(client, job_id):
    from qiskit_alice_bob_provider.remote.api.jobs import download_output

    output = download_output(client, job_id)
    if output is None:
        raise FelisError(f"job {job_id} has no output yet")
    return parse_counts(output)


def print_counts(job_id, result, as_json):
    if as_json:
        print_json({"id": job_id, "counts": result})
        return
    total = sum(result.values())
    if not total:
        print("(no counts)")
        return
    width = max(len(bits) for bits in result)
    for bits, count in sorted(result.items()):
        print(f"{bits:>{width}}  {count:>8}  {count / total:7.1%}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS. `test_counts_not_available_yet` passes because the provider's `download_output` returns `None` on HTTP 409.

- [ ] **Step 5: Checkpoint**

If the user has asked for commits:
```bash
git add cli/felis cli/test_felis.py
git commit -m "cli: add job submit, wait and counts helpers"
```

---

### Task 4: Job commands: `targets`, `run`, `status`, `result`, `cancel`

**Files:**
- Modify: `cli/felis`: add a `# ---- job commands` section after the jobs section, and change `COMMAND_GROUPS`.
- Test: `cli/test_felis.py` (append)

**Interfaces:**
- Consumes:
  - Task 1: `client_for`, `parse_params`, `eprint`, `print_json`.
  - Task 2: `check_input`, `read_input`.
  - Task 3: `submit`, `wait`, `check_succeeded`, `counts`, `print_counts`, `latest_event`, `format_event`, `describe_job_error`, `TERMINAL_EVENTS`.
- Produces:
  - Argument specs, as `(flags, kwargs)` tuples for `add_argument`: `ARG_TARGET`, `ARG_JOB_ID`, `ARG_PARAM`, `ARG_JSON`, `ARG_TIMEOUT`.
  - Helpers: `add_arguments(parser, *specs)`, `target_health(client, name) -> str`, `offer_cancel(client, job_id)`, `wait_for_result(client, job_id, args)`.
  - Commands: `cmd_targets`, `cmd_run`, `cmd_status`, `cmd_result`, `cmd_cancel`, and `add_job_commands(commands)`.

- [ ] **Step 1: Write the failing tests**

Append to `cli/test_felis.py`:
```python
# ---------------------------------------------------------------- job commands


@pytest.fixture
def succeeding_job(fake_client):
    fake_client.routes.update(
        {
            ("POST", "v1/jobs/"): job_with("CREATED"),
            ("GET", f"v1/jobs/{JOB_ID}"): sequence(
                job_with("CREATED", "INPUT_READY"),
                job_with("CREATED", "INPUT_READY", "EXECUTING", "SUCCEEDED"),
            ),
            ("GET", f"v1/jobs/{JOB_ID}/output"): b"0,49\n1,1\n",
        }
    )
    return fake_client


@pytest.fixture
def qir_file(tmp_path):
    path = tmp_path / "c.ll"
    path.write_text("QIR TEXT")
    return path


def test_targets_table_survives_failing_health(fake_client, capsys):
    fake_client.routes.update(
        {
            ("GET", "v1/targets/"): [{"name": "A", "numQubits": 1}, {"name": "BB", "numQubits": 40}],
            ("GET", "v1/targets/A/health"): "OK",
            ("GET", "v1/targets/BB/health"): api_error(500, "boom"),
        }
    )
    assert felis.main(["targets"]) == 0
    lines = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert lines == [["NAME", "QUBITS", "HEALTH"], ["A", "1", "OK"], ["BB", "40", "?"]]


def test_targets_json(fake_client, capsys):
    fake_client.routes.update(
        {
            ("GET", "v1/targets/"): [{"name": "A", "numQubits": 1}],
            ("GET", "v1/targets/A/health"): "NOK",
        }
    )
    assert felis.main(["targets", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == [{"name": "A", "numQubits": 1, "health": "NOK"}]


def test_run_ll_waits_and_prints_counts(succeeding_job, qir_file, capsys):
    assert felis.main(["run", "T", str(qir_file), "--shots", "50"]) == 0
    captured = capsys.readouterr()
    assert [line.split()[:2] for line in captured.out.splitlines()] == [["0", "49"], ["1", "1"]]
    assert "SUCCEEDED" in captured.err
    method, endpoint, kwargs = succeeding_job.calls[0]
    assert (method, endpoint) == ("POST", "v1/jobs/")
    assert kwargs["json"]["inputParams"] == {"nbShots": 50}
    assert succeeding_job.calls[1] == (
        "POST",
        f"v1/jobs/{JOB_ID}/input",
        {"files": {"input": "QIR TEXT"}},
    )


def test_run_py_transpiles_first(succeeding_job, monkeypatch):
    monkeypatch.setattr(felis, "to_qir", lambda circuit, target, client: "FROM PY")
    assert felis.main(["run", "T", str(HERE / "circuit.py")]) == 0
    assert succeeding_job.calls[1][2] == {"files": {"input": "FROM PY"}}


def test_run_no_wait_prints_only_job_id(succeeding_job, qir_file, capsys):
    assert felis.main(["run", "T", str(qir_file), "--no-wait"]) == 0
    assert capsys.readouterr().out == f"{JOB_ID}\n"
    assert all(endpoint != f"v1/jobs/{JOB_ID}" for _, endpoint, _ in succeeding_job.calls)


def test_run_json(succeeding_job, qir_file, capsys):
    assert felis.main(["run", "T", str(qir_file), "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"id": JOB_ID, "counts": {"0": 49, "1": 1}}


def test_run_failed_job_exits_2(fake_client, qir_file, capsys):
    fake_client.routes.update(
        {
            ("POST", "v1/jobs/"): job_with("CREATED"),
            ("GET", f"v1/jobs/{JOB_ID}"): job_with(
                "CREATED", "COMPILATION_FAILED", errors=[{"message": "bad QIR"}]
            ),
        }
    )
    assert felis.main(["run", "T", str(qir_file)]) == 2
    assert "ended with COMPILATION_FAILED: bad QIR" in capsys.readouterr().err


def test_run_timeout_exits_3(fake_client, qir_file, capsys):
    fake_client.routes.update(
        {
            ("POST", "v1/jobs/"): job_with("CREATED"),
            ("GET", f"v1/jobs/{JOB_ID}"): job_with("CREATED", "INPUT_READY"),
        }
    )
    assert felis.main(["run", "T", str(qir_file), "--timeout", "3"]) == 3
    assert f"{JOB_ID} is still running" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["c.txt", "missing.ll"])
def test_run_bad_input_fails_before_needing_key(tmp_path, capsys, name):  # Review Focus 4
    (tmp_path / "c.txt").write_text("x")
    assert felis.main(["run", "T", str(tmp_path / name)]) == 1
    err = capsys.readouterr().err
    assert name in err
    assert "API key" not in err


def test_run_interrupt_non_interactive_leaves_job(  # Review Focus 5
    succeeding_job, qir_file, monkeypatch, capsys
):
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(felis, "wait", interrupted)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert felis.main(["run", "T", str(qir_file)]) == 130
    assert f"job {JOB_ID} is still running" in capsys.readouterr().err
    assert all(method != "DELETE" for method, _, _ in succeeding_job.calls)


def test_run_interrupt_interactive_can_cancel(succeeding_job, qir_file, monkeypatch, capsys):
    class TtyInput(io.StringIO):
        def isatty(self):
            return True

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(felis, "wait", interrupted)
    monkeypatch.setattr(sys, "stdin", TtyInput("y\n"))
    assert felis.main(["run", "T", str(qir_file)]) == 130
    assert ("DELETE", f"v1/jobs/{JOB_ID}", {}) in succeeding_job.calls
    assert f"cancelled job {JOB_ID}" in capsys.readouterr().err


def test_status_text(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "EXECUTING")
    assert felis.main(["status", JOB_ID]) == 0
    out = capsys.readouterr().out
    assert "state:  EXECUTING" in out
    assert "2026-10-20T10:00:00  CREATED" in out


def test_status_json(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED")
    assert felis.main(["status", JOB_ID, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == job_with("CREATED")


def test_result_unfinished_without_wait(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "EXECUTING")
    assert felis.main(["result", JOB_ID]) == 1
    assert "has not finished (state: EXECUTING)" in capsys.readouterr().err


def test_result_finished(succeeding_job, capsys):
    succeeding_job.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "SUCCEEDED")
    assert felis.main(["result", JOB_ID, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"0": 49, "1": 1}


def test_result_wait(succeeding_job, capsys):
    assert felis.main(["result", JOB_ID, "--wait"]) == 0
    assert "49" in capsys.readouterr().out


def test_cancel(fake_client):
    assert felis.main(["cancel", JOB_ID]) == 0
    assert fake_client.calls == [("DELETE", f"v1/jobs/{JOB_ID}", {})]
```

Also add `import io` to the imports at the top of `cli/test_felis.py`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd cli && .venv/bin/pytest -v -k "targets or run or status or result or cancel"`
Expected: FAIL. argparse reports `invalid choice: 'targets'` (and the same for `run`, etc.) and exits with code 2, so the tests fail with `SystemExit: 2`.

- [ ] **Step 3: Write the minimal implementation**

In `cli/felis`, insert this section before `# ---------------------------------------------------------------- CLI`:
```python
# ---------------------------------------------------------------- job commands

ARG_TARGET = (("target",), {"help": "Felis target, e.g. EMU:1Q:LESCANNE_2020"})
ARG_JOB_ID = (("job_id",), {"help": "job ID"})
ARG_PARAM = (
    ("--param",),
    {
        "action": "append",
        "default": [],
        "metavar": "KEY=VALUE",
        "help": "set an input parameter (VALUE is parsed as JSON); repeatable",
    },
)
ARG_JSON = (("--json",), {"action": "store_true", "help": "print JSON instead of text"})
ARG_TIMEOUT = (
    ("--timeout",),
    {
        "type": float,
        "metavar": "S",
        "help": "stop waiting after S seconds (the job keeps running)",
    },
)


def add_arguments(parser, *specs):
    for flags, kwargs in specs:
        parser.add_argument(*flags, **kwargs)


def target_health(client, name):
    from qiskit_alice_bob_provider.remote.api.client import AliceBobApiException

    try:
        return client.get(f"v1/targets/{name}/health").json()
    except AliceBobApiException:
        return "?"  # one target's health shouldn't break the listing


def offer_cancel(client, job_id):
    """On Ctrl-C: ask whether to cancel (interactive only), else leave the job running."""
    from qiskit_alice_bob_provider.remote.api.jobs import cancel_job

    if sys.stdin.isatty():
        eprint(f"\ncancel job {job_id}? [y/N] ", end="", flush=True)
        if sys.stdin.readline().strip().lower() in ("y", "yes"):
            cancel_job(client, job_id)
            eprint(f"cancelled job {job_id}")
            return
    eprint(f"job {job_id} is still running")


def wait_for_result(client, job_id, args):
    """Block until the job ends, printing events to stderr, then print its counts."""
    try:
        job = wait(client, job_id, timeout=args.timeout, on_event=lambda e: eprint(format_event(e)))
    except KeyboardInterrupt:
        offer_cancel(client, job_id)
        raise
    check_succeeded(job)
    print_counts(job_id, counts(client, job_id), args.json)


def cmd_targets(args):
    from qiskit_alice_bob_provider.remote.api.targets import list_targets

    client = client_for(args)
    rows = [
        {"name": t["name"], "numQubits": t.get("numQubits"), "health": target_health(client, t["name"])}
        for t in list_targets(client)
    ]
    if args.json:
        print_json(rows)
        return
    width = max([len("NAME")] + [len(row["name"]) for row in rows])
    print(f"{'NAME':<{width}}  QUBITS  HEALTH")
    for row in rows:
        print(f"{row['name']:<{width}}  {row['numQubits']!s:>6}  {row['health']}")


def cmd_run(args):
    check_input(args.input)  # fail on a bad path before asking for an API key
    params = parse_params(args.param, args.shots)
    client = client_for(args)
    qir = read_input(args.input, args.target, client)
    job_id = submit(client, args.target, qir, params)["id"]
    if args.no_wait:
        print(job_id)
        return
    eprint(f"job {job_id} submitted to {args.target}")
    wait_for_result(client, job_id, args)


def cmd_status(args):
    from qiskit_alice_bob_provider.remote.api.jobs import get_job

    job = get_job(client_for(args), args.job_id)
    if args.json:
        print_json(job)
        return
    print(f"job:    {job['id']}")
    print(f"target: {job.get('target')}")
    print(f"state:  {latest_event(job)}")
    for event in job.get("events") or []:
        print(format_event(event))
    for error in job.get("errors") or []:
        print(f"  error: {describe_job_error(error)}")


def cmd_result(args):
    from qiskit_alice_bob_provider.remote.api.jobs import get_job

    client = client_for(args)
    if args.wait:
        wait_for_result(client, args.job_id, args)
        return
    job = get_job(client, args.job_id)
    state = latest_event(job)
    if state not in TERMINAL_EVENTS:
        raise FelisError(
            f"job {args.job_id} has not finished (state: {state}); use --wait to block"
        )
    check_succeeded(job)
    print_counts(args.job_id, counts(client, args.job_id), args.json)


def cmd_cancel(args):
    from qiskit_alice_bob_provider.remote.api.jobs import cancel_job

    cancel_job(client_for(args), args.job_id)
    eprint(f"cancelled job {args.job_id}")


def add_job_commands(commands):
    parser = commands.add_parser("targets", help="list targets with their health")
    add_arguments(parser, ARG_JSON)
    parser.set_defaults(func=cmd_targets)

    parser = commands.add_parser("run", help="submit a .ll or .py file and wait for the counts")
    add_arguments(parser, ARG_TARGET)
    parser.add_argument("input", help="QIR (.ll) or Python circuit (.py) file")
    parser.add_argument("--shots", type=int, help="number of shots (sets nbShots)")
    parser.add_argument("--no-wait", action="store_true", help="print the job ID and exit")
    add_arguments(parser, ARG_PARAM, ARG_TIMEOUT, ARG_JSON)
    parser.set_defaults(func=cmd_run)

    parser = commands.add_parser("status", help="show a job's state and events")
    add_arguments(parser, ARG_JOB_ID, ARG_JSON)
    parser.set_defaults(func=cmd_status)

    parser = commands.add_parser("result", help="print a finished job's counts")
    add_arguments(parser, ARG_JOB_ID)
    parser.add_argument("--wait", action="store_true", help="wait for the job to finish")
    add_arguments(parser, ARG_TIMEOUT, ARG_JSON)
    parser.set_defaults(func=cmd_result)

    parser = commands.add_parser("cancel", help="cancel a job")
    add_arguments(parser, ARG_JOB_ID)
    parser.set_defaults(func=cmd_cancel)
```

Change `COMMAND_GROUPS` to:
```python
COMMAND_GROUPS = (add_circuit_commands, add_job_commands)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Checkpoint**

If the user has asked for commits:
```bash
git add cli/felis cli/test_felis.py
git commit -m "cli: add felis targets, run, status, result and cancel"
```

---

### Task 5: API commands (`felis api …`)

**Files:**
- Modify: `cli/felis`: add a `# ---- API commands` section after the job commands section, and change `COMMAND_GROUPS`.
- Test: `cli/test_felis.py` (append)

**Interfaces:**
- Consumes:
  - Task 1: `client_for`, `parse_params`, `print_json`, `print_text`, `eprint`, `FelisError`.
  - Task 4: `ARG_TARGET`, `ARG_JOB_ID`, `ARG_PARAM`, `add_arguments`.
- Produces:
  - One `api_<name>(client, args)` handler per endpoint. Each returns a JSON value (printed as JSON), a `str` (printed as text), or `None` (nothing printed).
  - `download_handler(kind: str)`, which builds the four download handlers.
  - `API_COMMANDS`: a list of `(name, handler, help, argument_specs)`.
  - `cmd_api(args)` and `add_api_commands(commands)`.

- [ ] **Step 1: Write the failing tests**

Append to `cli/test_felis.py`:
```python
# ---------------------------------------------------------------- API commands

CREATE_PAYLOAD = {
    "inputDataFormat": "HUMAN_QIR",
    "outputDataFormat": "HISTOGRAM",
    "target": "T",
    "inputParams": {"nbShots": 10},
}


@pytest.mark.parametrize(
    "argv, method, endpoint, kwargs",
    [
        (["health"], "GET", "v1/health/", {}),
        (["list-targets"], "GET", "v1/targets/", {}),
        (["target-health", "T"], "GET", "v1/targets/T/health", {}),
        (
            ["target-availabilities", "T", "--limit", "5"],
            "GET",
            "v1/targets/T/availabilities",
            {"params": {"limit": 5, "offset": 0}},
        ),
        (["list-jobs", "--page", "2"], "GET", "v1/jobs/", {"params": {"page": 2, "limit": 100}}),
        (
            ["create-job", "T", "--param", "nbShots=10"],
            "POST",
            "v1/jobs/",
            {"json": CREATE_PAYLOAD},
        ),
        (["get-job", JOB_ID], "GET", f"v1/jobs/{JOB_ID}", {}),
        (["cancel-job", JOB_ID], "DELETE", f"v1/jobs/{JOB_ID}", {}),
        (["get-metrics", JOB_ID], "GET", f"v1/jobs/{JOB_ID}/metrics", {}),
        (["download-input", JOB_ID], "GET", f"v1/jobs/{JOB_ID}/input", {}),
        (["download-transpiled", JOB_ID], "GET", f"v1/jobs/{JOB_ID}/transpiled", {}),
        (["download-output", JOB_ID], "GET", f"v1/jobs/{JOB_ID}/output", {}),
        (["download-memory", JOB_ID], "GET", f"v1/jobs/{JOB_ID}/memory", {}),
    ],
)
def test_api_command_calls_endpoint(fake_client, argv, method, endpoint, kwargs):
    assert felis.main(["api", *argv]) == 0
    assert fake_client.calls == [(method, endpoint, kwargs)]


def test_api_get_job_prints_json(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED")
    assert felis.main(["api", "get-job", JOB_ID]) == 0
    assert json.loads(capsys.readouterr().out) == job_with("CREATED")


def test_api_download_prints_raw_content(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}/output")] = b"0,50\n"
    assert felis.main(["api", "download-output", JOB_ID]) == 0
    assert capsys.readouterr().out == "0,50\n"


def test_api_health_prints_text(fake_client, capsys):
    fake_client.routes[("GET", "v1/health/")] = b"OK"
    assert felis.main(["api", "health"]) == 0
    assert capsys.readouterr().out == "OK\n"


def test_api_download_not_ready(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}/output")] = api_error(409, "not ready")
    assert felis.main(["api", "download-output", JOB_ID]) == 1
    assert capsys.readouterr().err == f"felis: job {JOB_ID} has no output yet\n"


def test_api_upload_input(fake_client, qir_file):
    assert felis.main(["api", "upload-input", JOB_ID, str(qir_file)]) == 0
    assert fake_client.calls == [
        ("POST", f"v1/jobs/{JOB_ID}/input", {"files": {"input": "QIR TEXT"}})
    ]


def test_api_cancel_job_with_empty_body(fake_client, capsys):
    fake_client.routes[("DELETE", f"v1/jobs/{JOB_ID}")] = b""
    assert felis.main(["api", "cancel-job", JOB_ID]) == 0
    assert capsys.readouterr().out == ""


def test_api_error_is_one_line(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = api_error(401, '{"detail":"Not authenticated"}')
    assert felis.main(["api", "get-job", JOB_ID]) == 1
    assert capsys.readouterr().err == "felis: HTTP 401: Not authenticated\n"


def test_unexpected_error_is_one_line_unless_debug(fake_client, capsys):
    fake_client.routes[("GET", "v1/targets/")] = RuntimeError("boom")
    assert felis.main(["api", "list-targets"]) == 1
    assert capsys.readouterr().err == "felis: RuntimeError: boom\n"
    assert felis.main(["--debug", "api", "list-targets"]) == 1
    assert "Traceback" in capsys.readouterr().err


def test_bare_api_prints_api_help(capsys):
    assert felis.main(["api"]) == 1
    assert "API_COMMAND" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd cli && .venv/bin/pytest -v -k "api"`
Expected: FAIL with `invalid choice: 'api'` (SystemExit 2).

- [ ] **Step 3: Write the minimal implementation**

In `cli/felis`, insert this section before `# ---------------------------------------------------------------- CLI`:
```python
# ---------------------------------------------------------------- API commands
#
# One command per Felis API endpoint, for debugging. Each handler returns a
# JSON value (printed as JSON), a str (printed as-is) or None (nothing).


def api_health(client, args):
    return client.get("v1/health/").text


def api_list_targets(client, args):
    from qiskit_alice_bob_provider.remote.api.targets import list_targets

    return list_targets(client)


def api_target_health(client, args):
    return client.get(f"v1/targets/{args.target}/health").json()


def api_target_availabilities(client, args):
    params = {"limit": args.limit, "offset": args.offset}
    return client.get(f"v1/targets/{args.target}/availabilities", params=params).json()


def api_list_jobs(client, args):
    params = {"page": args.page, "limit": args.limit}
    return client.get("v1/jobs/", params=params).json()


def api_create_job(client, args):
    from qiskit_alice_bob_provider.remote.api.jobs import create_job

    return create_job(client, args.target, parse_params(args.param))


def api_upload_input(client, args):
    from qiskit_alice_bob_provider.remote.api.jobs import upload_input

    path = Path(args.file)
    if not path.is_file():
        raise FelisError(f"{path}: no such file")
    upload_input(client, args.job_id, path.read_text())
    eprint(f"uploaded {path} to job {args.job_id}")


def api_get_job(client, args):
    from qiskit_alice_bob_provider.remote.api.jobs import get_job

    return get_job(client, args.job_id)


def api_cancel_job(client, args):
    # The provider's cancel_job discards the response; call the endpoint
    # directly so the cancelled job is shown.
    response = client.delete(f"v1/jobs/{args.job_id}")
    return response.json() if response.content else None


def api_get_metrics(client, args):
    from qiskit_alice_bob_provider.remote.api.jobs import get_job_metrics

    return get_job_metrics(client, args.job_id)


def download_handler(kind):
    """Handler for GET v1/jobs/{id}/<kind>, via the provider's download_<kind>."""

    def handler(client, args):
        from qiskit_alice_bob_provider.remote.api import jobs

        content = getattr(jobs, f"download_{kind}")(client, args.job_id)
        if content is None:  # the API answers 409 until the file exists
            raise FelisError(f"job {args.job_id} has no {kind} yet")
        return content

    return handler


ARG_LIMIT = (("--limit",), {"type": int, "default": 100, "help": "page size (default: 100)"})
ARG_OFFSET = (("--offset",), {"type": int, "default": 0, "help": "records to skip"})
ARG_PAGE = (("--page",), {"type": int, "default": 1, "help": "page number (default: 1)"})
ARG_FILE = (("file",), {"help": "QIR (.ll) file"})

API_COMMANDS = [
    ("health", api_health, "GET v1/health/", []),
    ("list-targets", api_list_targets, "GET v1/targets/", []),
    ("target-health", api_target_health, "GET v1/targets/{target}/health", [ARG_TARGET]),
    (
        "target-availabilities",
        api_target_availabilities,
        "GET v1/targets/{target}/availabilities",
        [ARG_TARGET, ARG_LIMIT, ARG_OFFSET],
    ),
    ("list-jobs", api_list_jobs, "GET v1/jobs/", [ARG_PAGE, ARG_LIMIT]),
    ("create-job", api_create_job, "POST v1/jobs/ (no input)", [ARG_TARGET, ARG_PARAM]),
    ("upload-input", api_upload_input, "POST v1/jobs/{id}/input", [ARG_JOB_ID, ARG_FILE]),
    ("get-job", api_get_job, "GET v1/jobs/{id}", [ARG_JOB_ID]),
    ("cancel-job", api_cancel_job, "DELETE v1/jobs/{id}", [ARG_JOB_ID]),
    ("get-metrics", api_get_metrics, "GET v1/jobs/{id}/metrics", [ARG_JOB_ID]),
    ("download-input", download_handler("input"), "GET v1/jobs/{id}/input", [ARG_JOB_ID]),
    (
        "download-transpiled",
        download_handler("transpiled"),
        "GET v1/jobs/{id}/transpiled",
        [ARG_JOB_ID],
    ),
    ("download-output", download_handler("output"), "GET v1/jobs/{id}/output", [ARG_JOB_ID]),
    ("download-memory", download_handler("memory"), "GET v1/jobs/{id}/memory", [ARG_JOB_ID]),
]


def cmd_api(args):
    result = args.api_handler(client_for(args), args)
    if result is None:
        return
    if isinstance(result, str):
        print_text(result)
    else:
        print_json(result)


def add_api_commands(commands):
    api = commands.add_parser("api", help="call one Felis API endpoint (for debugging)")
    api.set_defaults(help_parser=api)
    api_commands = api.add_subparsers(title="API commands", metavar="API_COMMAND")
    for name, handler, help_text, specs in API_COMMANDS:
        parser = api_commands.add_parser(name, help=help_text)
        add_arguments(parser, *specs)
        parser.set_defaults(func=cmd_api, api_handler=handler)
```

Change `COMMAND_GROUPS` to:
```python
COMMAND_GROUPS = (add_circuit_commands, add_job_commands, add_api_commands)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS. If `test_bare_api_prints_api_help` prints the top-level help instead, the `help_parser` default was not set on the `api` parser.

- [ ] **Step 5: Checkpoint**

If the user has asked for commits:
```bash
git add cli/felis cli/test_felis.py
git commit -m "cli: add felis api commands, one per endpoint"
```

---

### Task 6: Docs, module-2 migration and smoke test

**Files:**
- Create: `cli/README.md`
- Modify: `hands-on/module-2/alice-bob/README.md` (the "Optional: Generate QIR input file" section)
- Delete: `hands-on/module-2/alice-bob/generate_input.py`
- Modify: `cli/DESIGN.md` (file list)

**Interfaces:**
- Consumes: the finished `cli/felis`.
- Produces: user documentation. No code.

- [ ] **Step 1: Write `cli/README.md`**

````markdown
# felis

A command-line client for Alice & Bob's Felis API: generate QIR from Qiskit circuits, run them on Felis targets, and call any API endpoint directly.

## Install

Requires Python 3.11 or 3.12.

```shell-session
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

`felis` is a single script. Run it as `./felis` from this directory, or put this directory on your `PATH`.

## Configure

| Env var | Flag | Default |
|---|---|---|
| `QRMI_AB_FELIS_API_KEY` | `--api-key` | (required) |
| `QRMI_AB_FELIS_BASE_ENDPOINT` | `--endpoint` | `https://api.alice-bob.com/` |

Variables are read from the environment, or from a `.env` file in the current directory. These are the same names the QRMI examples use, so one `.env` works for both.

## Use

```shell-session
felis targets                                          # list targets and their health
felis generate EMU:1Q:LESCANNE_2020 circuit.py > c.ll  # Python circuit -> QIR
felis run EMU:1Q:LESCANNE_2020 circuit.py --shots 100  # transpile, submit, wait, print counts
felis run EMU:1Q:LESCANNE_2020 c.ll --no-wait          # submit QIR, print the job ID
felis status JOB_ID
felis result JOB_ID --wait
felis cancel JOB_ID
```

A circuit file is a Python file that defines a top-level `circuit` (a `qiskit.QuantumCircuit`). See [`circuit.py`](circuit.py).

Set target input parameters with `--param KEY=VALUE` (repeatable; values are parsed as JSON), e.g. `--param averageNbPhotons=4`.

Add `--json` to `targets`, `run`, `status` and `result` for machine-readable output.

### API commands

`felis api` has one command per API endpoint, printing the raw response. Run `felis api --help` for the list, for example:

```shell-session
felis api list-jobs --limit 10
felis api create-job EMU:1Q:LESCANNE_2020 --param nbShots=10
felis api upload-input JOB_ID c.ll
felis api download-transpiled JOB_ID
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage, configuration or API error |
| 2 | The job failed, was cancelled, or timed out on the server |
| 3 | `--timeout` expired (the job is still running) |
| 130 | Interrupted |

Add `--debug` (before the command, e.g. `felis --debug run …`) to see full tracebacks.

## Tests

```shell-session
pip install -r requirements-dev.txt
pytest
```
````

- [ ] **Step 2: Update the module-2 README and delete `generate_input.py`**

In `hands-on/module-2/alice-bob/README.md`, replace:
````markdown
You can modify the Qiskit circuit in `generate_input.py` before generating it in QIR form as follows:

```shell-session
python generate_input.py EMU:1Q:LESCANNE_2020 > generated_circuit.ll
```
````
with:
````markdown
Use the [`felis` CLI](../../../cli/README.md) to transpile a Qiskit circuit into QIR. You can modify the circuit in [`cli/circuit.py`](../../../cli/circuit.py) first:

```shell-session
../../../cli/felis generate EMU:1Q:LESCANNE_2020 ../../../cli/circuit.py > generated_circuit.ll
```
````

Then run: `git rm hands-on/module-2/alice-bob/generate_input.py`. If it isn't tracked, use plain `rm` instead.

Check that nothing else refers to it:
```bash
grep -rn "generate_input" --include="*.md" --include="*.py" . | grep -v .venv | grep -v "^./cli/"
```
Expected: no output.

- [ ] **Step 3: Update the spec's file list**

In `cli/DESIGN.md`, replace the code block under "## Location and files" with:
```
cli/
  felis                  # the script (shebang, executable)
  circuit.py             # example circuit: top-level `circuit` variable
  requirements.txt       # runtime dependencies
  requirements-dev.txt   # + pytest
  test_felis.py          # unit tests (pytest, fake API client)
  testdata/              # captured target description + expected QIR
  README.md              # install and usage
  DESIGN.md              # this file
  PLAN.md                # implementation plan
```

- [ ] **Step 4: Run the full test suite**

Run: `cd cli && .venv/bin/pytest -v`
Expected: all tests PASS.

- [ ] **Step 5: Smoke-test against the real API**

Use the module-2 venv: it already has the provider and dotenv installed, and its `.env` has a key.
```bash
cd hands-on/module-2/alice-bob && source .venv/bin/activate
F=../../../cli/felis

$F generate EMU:1Q:LESCANNE_2020 ../../../cli/circuit.py | diff - generated_circuit.ll && echo SAME
# expect: SAME

$F targets
# expect: a table including EMU:1Q:LESCANNE_2020 with health OK

$F run EMU:1Q:LESCANNE_2020 ../../../cli/circuit.py --shots 50
# expect: events on stderr ending in SUCCEEDED, then a counts table summing to 50

JOB=$($F run EMU:1Q:LESCANNE_2020 generated_circuit.ll --no-wait); echo $JOB
$F status $JOB
$F result $JOB --wait --json
# expect: {"id": "...", "counts": {...}}

$F api health                                   # expect: OK
$F api list-targets | head
$F api target-health EMU:1Q:LESCANNE_2020       # expect: OK
$F api target-availabilities EMU:1Q:LESCANNE_2020 --limit 2
$F api list-jobs --limit 2
$F api get-job $JOB
$F api get-metrics $JOB
$F api download-input $JOB
$F api download-transpiled $JOB
$F api download-output $JOB
$F api download-memory $JOB                     # expect: "has no memory yet" unless memory=true was set

NEW=$($F api create-job EMU:1Q:LESCANNE_2020 --param nbShots=10 | python -c "import json,sys; print(json.load(sys.stdin)['id'])")
$F api upload-input $NEW generated_circuit.ll
$F api cancel-job $NEW                          # expect: job JSON (or nothing) and exit 0; it may already have finished
$F api get-job not-a-uuid; echo "exit=$?"       # expect: "felis: HTTP 400: Field path->job_id: ..." and exit=1

python example.py ab_emu_1q_lescanne_2020 generated_circuit.ll
# expect: the module-2 QRMI example still works unchanged
```

Record anything that differs from the expected output. Fix it, with a regression test, before checking off this step.

- [ ] **Step 6: Checkpoint**

If the user has asked for commits:
```bash
git add cli/README.md cli/DESIGN.md hands-on/module-2/alice-bob/README.md
git commit -m "cli: document felis and use it for module-2 QIR generation"
```
