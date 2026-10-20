"""Tests for the felis CLI. Run with: cd cli && .venv/bin/pytest -v"""

import importlib.machinery
import importlib.util
import io
import json
import re
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
    callable taking the request's keyword arguments and returning either.
    Unknown routes return {}.
    """

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def _request(self, method, endpoint, **kwargs):
        self.calls.append((method, endpoint, kwargs))
        body = self.routes.get((method, endpoint), {})
        if callable(body):
            body = body(**kwargs)
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
    assert (config.api_key, config.api_key_source) == ("from-dotenv", "./.env")
    assert (config.endpoint, config.endpoint_source) == ("https://example.test/", "./.env")


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


# ---------------------------------------------------------------- circuits

TARGETS = json.loads((HERE / "testdata" / "targets.json").read_text())
LESCANNE = "EMU:1Q:LESCANNE_2020"


def normalise_qir(qir):
    """Qiskit names unnamed circuits circuit-<N> from a process-wide counter."""
    return re.sub(r"circuit-\d+", "circuit-N", qir).rstrip("\n")


EXPECTED_QIR = normalise_qir((HERE / "testdata" / "expected_lescanne.ll").read_text())


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
    assert capsys.readouterr().err == f"felis: {path}: ValueError: oops\n"


def test_find_target_unknown_lists_available():  # Review Focus 2
    client = FakeClient({("GET", "v1/targets/"): [{"name": "A"}, {"name": "B"}]})
    with pytest.raises(felis.FelisError, match="unknown target 'C'; available targets: A, B"):
        felis.find_target(client, "C")


def test_to_qir_matches_expected_output():
    client = FakeClient({("GET", "v1/targets/"): TARGETS})
    qir = felis.to_qir(felis.load_circuit(HERE / "circuit.py"), LESCANNE, client)
    assert normalise_qir(qir) == EXPECTED_QIR


def test_generate_writes_output_file(tmp_path, fake_client):
    fake_client.routes[("GET", "v1/targets/")] = TARGETS
    out = tmp_path / "out.ll"
    assert felis.main(["generate", LESCANNE, str(HERE / "circuit.py"), "-o", str(out)]) == 0
    assert normalise_qir(out.read_text()) == EXPECTED_QIR


def test_read_input_ll_is_passed_through(tmp_path):
    path = tmp_path / "c.ll"
    path.write_text("QIR TEXT")
    assert felis.read_input(path, LESCANNE, client=None) == "QIR TEXT"


@pytest.mark.parametrize("name", ["c.txt", "missing.ll"])
def test_check_input_rejects(tmp_path, name):
    (tmp_path / "c.txt").write_text("x")
    with pytest.raises(felis.FelisError):
        felis.check_input(tmp_path / name)


def test_generate_loads_circuit_using_provider_instructions(tmp_path):
    # circuit.py uses measure_x, which only exists once the provider is imported.
    # A fresh process gets past loading the circuit and stops at the missing key.
    result = subprocess.run(
        [sys.executable, str(FELIS), "generate", LESCANNE, str(HERE / "circuit.py")],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "no API key" in result.stderr


# ---------------------------------------------------------------- jobs


def job_with(*types, errors=()):
    events = [{"type": t, "createdAt": f"2026-10-20T10:00:0{i}"} for i, t in enumerate(types)]
    return {"id": JOB_ID, "target": "T", "events": events, "errors": list(errors)}


def sequence(*bodies):
    """A route body that returns each item in turn, then repeats the last."""
    remaining = list(bodies)
    return lambda **_: remaining.pop(0) if len(remaining) > 1 else remaining[0]


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
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    assert felis.main(["run", "T", str(qir_file)]) == 130
    assert ("DELETE", f"v1/jobs/{JOB_ID}", {}) in succeeding_job.calls
    assert f"cancelled job {JOB_ID}" in capsys.readouterr().err


def test_status_text(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "EXECUTING")
    assert felis.main(["job", "status", JOB_ID]) == 0
    out = capsys.readouterr().out
    assert "state:  EXECUTING" in out
    assert "2026-10-20T10:00:00  CREATED" in out


def test_status_json(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED")
    assert felis.main(["job", "status", JOB_ID, "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == job_with("CREATED")


def test_result_unfinished_without_wait(fake_client, capsys):
    fake_client.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "EXECUTING")
    assert felis.main(["job", "result", JOB_ID]) == 1
    assert "has not finished (state: EXECUTING)" in capsys.readouterr().err


def test_result_finished(succeeding_job, capsys):
    succeeding_job.routes[("GET", f"v1/jobs/{JOB_ID}")] = job_with("CREATED", "SUCCEEDED")
    assert felis.main(["job", "result", JOB_ID, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["counts"] == {"0": 49, "1": 1}


def test_result_wait(succeeding_job, capsys):
    assert felis.main(["job", "result", JOB_ID, "--wait"]) == 0
    assert "49" in capsys.readouterr().out


def test_cancel(fake_client):
    assert felis.main(["job", "cancel", JOB_ID]) == 0
    assert fake_client.calls == [("DELETE", f"v1/jobs/{JOB_ID}", {})]


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


# ---------------------------------------------------------------- review fixes


def test_config_strips_whitespace_around_key(tmp_path):
    # e.g. a key file written with `echo key > file` and mounted from a Secret
    (tmp_path / ".env").write_text('QRMI_AB_FELIS_API_KEY="sekret123\n"\n')
    assert felis.load_config(parse([])).api_key == "sekret123"


@pytest.mark.parametrize("key", ["sek ret", "sek\nret", "sek\x00ret"])
def test_config_rejects_bad_key_without_echoing_it(key):
    with pytest.raises(felis.FelisError) as raised:
        felis.load_config(parse(["--api-key", key]))
    assert "invalid characters" in str(raised.value)
    assert "sek" not in str(raised.value)


@pytest.mark.parametrize("argv", [["run"], ["api", "get-job"], ["run", "T", "c.ll", "--bogus"]])
def test_usage_errors_exit_1(argv, capsys):
    with pytest.raises(SystemExit) as raised:
        felis.main(argv)
    assert raised.value.code == 1
    assert "usage:" in capsys.readouterr().err


def test_run_interrupt_does_not_prompt_when_stderr_redirected(
    succeeding_job, qir_file, monkeypatch, capsys
):
    class TtyInput(io.StringIO):
        def isatty(self):
            return True

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(felis, "wait", interrupted)
    monkeypatch.setattr(sys, "stdin", TtyInput("y\n"))  # stderr is captured: not a TTY
    assert felis.main(["run", "T", str(qir_file)]) == 130
    err = capsys.readouterr().err
    assert "cancel job" not in err
    assert f"job {JOB_ID} is still running" in err
    assert all(method != "DELETE" for method, _, _ in succeeding_job.calls)


# ---------------------------------------------------------------- minor review fixes


def test_circuit_file_exit_is_reported_not_propagated(tmp_path, capsys):
    path = tmp_path / "exits.py"
    path.write_text("import sys\nsys.exit(5)\n")
    assert felis.main(["generate", LESCANNE, str(path)]) == 1
    assert capsys.readouterr().err == f"felis: {path}: SystemExit: 5\n"


def test_circuit_file_can_import_siblings(tmp_path):
    (tmp_path / "helper_mod_for_felis.py").write_text(
        "from qiskit import QuantumCircuit\ncircuit = QuantumCircuit(1, 1)\n"
    )
    path = tmp_path / "uses_helper.py"
    path.write_text("from helper_mod_for_felis import circuit\n")
    assert felis.load_circuit(path).num_qubits == 1
    assert str(tmp_path) not in sys.path


def test_failed_upload_names_the_created_job():
    client = FakeClient(
        {
            ("POST", "v1/jobs/"): {"id": JOB_ID},
            ("POST", f"v1/jobs/{JOB_ID}/input"): api_error(400, '{"detail":"bad input"}'),
        }
    )
    with pytest.raises(felis.FelisError) as raised:
        felis.submit(client, "T", "QIR", {})
    assert str(raised.value) == (
        f"job {JOB_ID} was created but uploading its input failed: HTTP 400: bad input"
    )


def test_wait_survives_transient_errors():
    client = FakeClient(
        {
            ("GET", f"v1/jobs/{JOB_ID}"): sequence(
                api_error(502, "Bad Gateway"),
                requests.ConnectionError("reset"),
                job_with("CREATED", "SUCCEEDED"),
            )
        }
    )
    assert felis.latest_event(felis.wait(client, JOB_ID)) == "SUCCEEDED"


def test_wait_gives_up_after_repeated_transient_errors_naming_job():
    client = FakeClient({("GET", f"v1/jobs/{JOB_ID}"): api_error(503, "Unavailable")})
    with pytest.raises(felis.FelisError) as raised:
        felis.wait(client, JOB_ID)
    assert f"job {JOB_ID}" in str(raised.value)
    assert "may still be running" in str(raised.value)
    assert "HTTP 503" in str(raised.value)


def test_wait_does_not_retry_client_errors():
    from qiskit_alice_bob_provider.remote.api.client import AliceBobApiException

    client = FakeClient({("GET", f"v1/jobs/{JOB_ID}"): api_error(404, "Not Found")})
    with pytest.raises(AliceBobApiException):
        felis.wait(client, JOB_ID)
    assert len(client.calls) == 1


def test_describe_api_error_is_one_short_line():
    html = "<html>\n<head><title>502 Bad Gateway</title></head>\n<body>" + "x" * 500 + "</body>\n</html>"
    message = felis.describe_api_error(api_error(502, html))
    assert "\n" not in message
    assert message.startswith("HTTP 502: <html> <head><title>502 Bad Gateway</title>")
    assert len(message) <= 220


def test_describe_api_error_empty_body_uses_reason():
    assert felis.describe_api_error(api_error(401, "")) == "HTTP 401: Unauthorized"


@pytest.mark.parametrize(
    "endpoint", ["https://evil.test/", "https://alice-bob.com.evil.test/", "http://127.0.0.1:8080/"]
)
def test_foreign_endpoint_warns_when_key_will_be_sent(tmp_path, capsys, endpoint):
    (tmp_path / ".env").write_text(
        f"QRMI_AB_FELIS_API_KEY=key\nQRMI_AB_FELIS_BASE_ENDPOINT={endpoint}\n"
    )
    felis.client_for(parse([]))
    assert capsys.readouterr().err == (
        f"felis: warning: sending your API key to {endpoint} (from ./.env)\n"
    )


@pytest.mark.parametrize(
    "endpoint", ["https://api.alice-bob.com/", "https://api-staging.alice-bob.com/"]
)
def test_alice_bob_endpoints_do_not_warn(tmp_path, capsys, endpoint):
    (tmp_path / ".env").write_text(
        f"QRMI_AB_FELIS_API_KEY=key\nQRMI_AB_FELIS_BASE_ENDPOINT={endpoint}\n"
    )
    felis.client_for(parse([]))
    assert capsys.readouterr().err == ""


def test_config_command_shows_settings_and_sources(tmp_path, monkeypatch, capsys):
    (tmp_path / ".env").write_text(
        "QRMI_AB_FELIS_BASE_ENDPOINT=https://api-staging.alice-bob.com/\n"
    )
    monkeypatch.setenv(felis.API_KEY_VAR, "abcd1234secretwxyz")
    assert felis.main(["config"]) == 0
    captured = capsys.readouterr()
    lines = [line.split() for line in captured.out.splitlines()]
    assert lines == [
        ["endpoint", "https://api-staging.alice-bob.com/", "(from", "./.env)"],
        ["api", "key", "abcd…wxyz", "(from", "$QRMI_AB_FELIS_API_KEY)"],
    ]
    assert "secret" not in captured.out + captured.err


def test_config_command_default_endpoint_and_flag_key(capsys):
    assert felis.main(["--api-key", "abcd1234secretwxyz", "config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "endpoint": "https://api.alice-bob.com/",
        "endpoint_source": "default",
        "api_key": "abcd…wxyz",
        "api_key_source": "--api-key",
    }


def test_config_command_without_key(capsys):
    assert felis.main(["config"]) == 0
    out = capsys.readouterr().out
    assert "(not set)" in out
    assert "https://api.alice-bob.com/" in out


def test_config_command_masks_short_keys_entirely(capsys):
    assert felis.main(["--api-key", "short", "config", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["api_key"] == "****"


def test_default_endpoint_is_not_announced(monkeypatch, capsys):
    monkeypatch.setenv(felis.API_KEY_VAR, "key")
    felis.load_config(parse([]))
    assert capsys.readouterr().err == ""


def test_bare_job_prints_job_help(capsys):
    assert felis.main(["job"]) == 1
    assert "JOB_COMMAND" in capsys.readouterr().err


@pytest.mark.parametrize("old", ["status", "result", "cancel"])
def test_job_commands_are_not_top_level(old):
    with pytest.raises(SystemExit) as raised:
        felis.main([old, JOB_ID])
    assert raised.value.code == 1


# ---------------------------------------------------------------- job list


def listed(index, state, target="T"):
    job = job_with("CREATED", state)
    return {**job, "id": f"job-{index:03d}", "target": target}


def pages(*batches):
    """A v1/jobs/ route that answers each page from `batches` (1-based), then []."""
    return lambda params, **_: batches[params["page"] - 1] if params["page"] <= len(batches) else []


def test_job_list_shows_recent_jobs(fake_client, capsys):
    fake_client.routes[("GET", "v1/jobs/")] = [
        listed(1, "EXECUTING", target="EMU:1Q:LESCANNE_2020"),
        listed(2, "SUCCEEDED"),
    ]
    assert felis.main(["job", "list", "--all"]) == 0
    assert fake_client.calls == [("GET", "v1/jobs/", {"params": {"page": 1, "limit": 20}})]
    lines = [line.split() for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        ["ID", "TARGET", "STATE", "CREATED"],
        ["job-001", "EMU:1Q:LESCANNE_2020", "EXECUTING", "2026-10-20T10:00:00"],
        ["job-002", "T", "SUCCEEDED", "2026-10-20T10:00:00"],
    ]


def test_job_list_limit(fake_client):
    fake_client.routes[("GET", "v1/jobs/")] = []
    assert felis.main(["job", "list", "--all", "--limit", "5"]) == 0
    assert fake_client.calls == [("GET", "v1/jobs/", {"params": {"page": 1, "limit": 5}})]


def test_job_list_defaults_to_active_jobs_across_pages(fake_client, capsys):
    page1 = [listed(i, "SUCCEEDED") for i in range(99)] + [listed(99, "EXECUTING")]
    page2 = [listed(100, "CANCELLED"), listed(101, "INPUT_READY")]
    fake_client.routes[("GET", "v1/jobs/")] = pages(page1, page2)
    assert felis.main(["job", "list"]) == 0
    rows = capsys.readouterr().out.splitlines()[1:]
    assert [row.split()[0] for row in rows] == ["job-099", "job-101"]
    assert [kwargs["params"] for _, _, kwargs in fake_client.calls] == [
        {"page": 1, "limit": 100},
        {"page": 2, "limit": 100},
    ]


def test_job_list_active_stops_once_limit_reached(fake_client):
    page = [listed(0, "EXECUTING")] + [listed(i, "SUCCEEDED") for i in range(1, 100)]
    fake_client.routes[("GET", "v1/jobs/")] = pages(page, page)
    assert felis.main(["job", "list", "--limit", "1"]) == 0
    assert len(fake_client.calls) == 1


def test_job_list_active_scans_at_most_1000_jobs(fake_client, capsys):
    page = [listed(i, "SUCCEEDED") for i in range(100)]
    fake_client.routes[("GET", "v1/jobs/")] = lambda **_: page
    assert felis.main(["job", "list"]) == 0
    assert len(fake_client.calls) == 10
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no active jobs" in captured.err


def test_job_list_json(fake_client, capsys):
    jobs = [listed(1, "SUCCEEDED")]
    fake_client.routes[("GET", "v1/jobs/")] = jobs
    assert felis.main(["job", "list", "--all", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == jobs


def test_job_list_empty(fake_client, capsys):
    fake_client.routes[("GET", "v1/jobs/")] = []
    assert felis.main(["job", "list", "--all"]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "no jobs\n"


# ---------------------------------------------------------------- targets show

DESCRIBED_TARGET = {
    "name": "EMU:2Q:TEST",
    "numQubits": 2,
    "instructions": [
        {"signature": "__quantum__qis__x__body:void (%Qubit*)"},
        {"signature": "__quantum__qis__s__adj:void (%Qubit*)"},
        {"signature": "__quantum__qis__cx__body:void (%Qubit*, %Qubit*)"},
        {"signature": "__quantum__qis__read_result__body:i1 (%Result*)"},
        {"signature": "something_unexpected"},
    ],
    "inputParams": {
        "nbShots": {
            "required": True,
            "type": "int",
            "default": 1000,
            "constraints": [{"min": 1.0, "max": 10000000.0}],
        },
        "averageNbPhotons": {
            "required": True,
            "type": "float",
            "default": 4.0,
            "constraints": [{"min": 1.0, "max": 7.5}],
        },
        "kappa1": {"required": False, "type": "float", "default": None, "constraints": [{"min": 0.0}]},
        "memory": {"required": False, "type": "bool", "default": False, "constraints": []},
    },
}


@pytest.fixture
def described_target(fake_client):
    fake_client.routes.update(
        {
            ("GET", "v1/targets/"): [DESCRIBED_TARGET],
            ("GET", "v1/targets/EMU:2Q:TEST/health"): "OK",
        }
    )
    return fake_client


def test_targets_show(described_target, capsys):
    assert felis.main(["targets", "show", "EMU:2Q:TEST"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[:4] == [
        "target  EMU:2Q:TEST",
        "qubits  2",
        "health  OK",
        "gates   cx, s†, x, something_unexpected",
    ]
    assert [line.split() for line in lines[5:]] == [
        ["PARAMETER", "TYPE", "REQUIRED", "DEFAULT", "RANGE"],
        ["nbShots", "int", "yes", "1000", "1–10000000"],
        ["averageNbPhotons", "float", "yes", "4.0", "1–7.5"],
        ["kappa1", "float", "no", "≥", "0"],
        ["memory", "bool", "no", "false"],
    ]


def test_targets_show_json_is_raw_description(described_target, capsys):
    assert felis.main(["targets", "show", "EMU:2Q:TEST", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == DESCRIBED_TARGET


def test_targets_show_unknown_target_lists_available(described_target, capsys):
    assert felis.main(["targets", "show", "NOPE"]) == 1
    assert "available targets: EMU:2Q:TEST" in capsys.readouterr().err


def test_targets_show_real_description(fake_client, capsys):
    fake_client.routes[("GET", "v1/targets/")] = TARGETS
    assert felis.main(["targets", "show", LESCANNE]) == 0
    out = capsys.readouterr().out
    assert "averageNbPhotons" in out
    assert "read_result" not in out


def test_bare_targets_still_lists(fake_client, capsys):
    fake_client.routes.update(
        {("GET", "v1/targets/"): [{"name": "A", "numQubits": 1}], ("GET", "v1/targets/A/health"): "OK"}
    )
    assert felis.main(["targets"]) == 0
    assert capsys.readouterr().out.splitlines()[1].split() == ["A", "1", "OK"]
