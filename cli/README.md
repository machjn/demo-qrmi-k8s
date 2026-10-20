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

To see which endpoint and key are in use, and where each comes from, run `felis config`. The key is masked. If the endpoint is not an `alice-bob.com` host, every command that calls the API prints a warning, since your key is sent there.

## Use

```shell-session
felis config                                           # endpoint and (masked) key in use, and their sources
felis targets                                          # list targets and their health
felis targets show EMU:1Q:LESCANNE_2020               # a target's gates and input parameters
felis generate EMU:1Q:LESCANNE_2020 circuit.py > c.ll  # Qiskit circuit -> QIR
felis run EMU:1Q:LESCANNE_2020 circuit.py --shots 100  # transpile, submit, wait, print counts
felis run EMU:1Q:LESCANNE_2020 c.ll --no-wait          # submit QIR, print the job ID
felis job list [--all]                                 # unfinished jobs (--all: include finished ones)
felis job status JOB_ID
felis job result JOB_ID --wait
felis job cancel JOB_ID
```

Circuits are Qiskit circuits: a Python file that defines a `qiskit.QuantumCircuit` as a top-level variable named `circuit`. See [`circuit.py`](circuit.py).

Set target input parameters (see `felis targets show TARGET` for each target's parameters, defaults and ranges) with `--param KEY=VALUE` (repeatable; values are parsed as JSON), e.g. `--param averageNbPhotons=4`.

Add `--json` to `targets`, `run`, `job list`, `job status` and `job result` for machine-readable output.

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
