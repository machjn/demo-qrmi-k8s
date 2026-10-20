# `felis` CLI: design

## Purpose

`felis` is a single-script Python CLI for Alice & Bob's Felis API. It has two jobs:

1. Generate QIR from a Qiskit circuit, using the provider's transpilation (as `generate_input.py` does today).
2. Run circuits on Felis targets and inspect them, both through high-level commands and through one-to-one API calls for debugging.

It is built on `qiskit-alice-bob-provider` (≥ 3.0). Its first users are the QRMI/Kubernetes tutorial at SC26, but the scope isn't tied to the tutorial.

## Non-goals

- Generating commands from the OpenAPI spec. The spec is not reliably reachable, and there are only 14 endpoints, so they are written by hand.
- A generic "raw request" command. Every endpoint has its own command.
- Packaging as a pip-installable distribution.

## Location and files

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

`hands-on/module-2/alice-bob/generate_input.py` is replaced by `felis generate` and deleted, and the module-2 README is updated to use `felis`.

## Configuration

| Setting | Env var | Flag | Default |
|---|---|---|---|
| API key | `QRMI_AB_FELIS_API_KEY` | `--api-key` | (required) |
| Base endpoint | `QRMI_AB_FELIS_BASE_ENDPOINT` | `--endpoint` | `https://api.alice-bob.com/` |

Settings are resolved in order of precedence: a flag beats the environment, which beats a `.env` file in the current directory. The env var names match the QRMI examples, so one `.env` serves both. Commands that don't need the API (`--help`, argument errors) never ask for a key.

- `felis config` shows the resolved endpoint and API key, each with its source (`--endpoint`/`--api-key`, `$VAR`, `./.env` or `default`). The key is masked to its first and last four characters, or `****` if shorter than 12 characters. It works without a key set, showing `(not set)`.
- When the endpoint's host is not `alice-bob.com` or a subdomain of it, every command that builds an API client prints `felis: warning: sending your API key to <endpoint> (from <source>)` on stderr. This catches a `.env` in an untrusted checkout redirecting the key. Alice & Bob hosts, including staging, are silent.

## Commands

### Top-level commands

```
felis config [--json]
felis targets [--json]
felis generate TARGET CIRCUIT.py [-o OUT.ll]
felis run TARGET INPUT [--shots N] [--param k=v ...] [--no-wait] [--timeout S] [--json]
felis job list [--all] [--limit N] [--json]
felis job status JOB_ID [--json]
felis job result JOB_ID [--wait] [--timeout S] [--json]
felis job cancel JOB_ID
```

- `targets` prints a table of name, qubit count and health (from `v1/targets/{target}/health`).
- `generate` writes QIR to stdout, or to `-o`.
- `run`:
  - If `INPUT` ends in `.ll`, it is submitted as-is. If it ends in `.py`, it is loaded and transpiled to QIR first. Any other extension is an error.
  - By default `run` blocks: it prints each new job event as it appears, then the counts.
  - With `--no-wait` it prints the job ID and exits.
- `job list` prints unfinished jobs (at most `--limit`, default 20), newest first, as a table of ID, target, state and created time.
  - The API can't filter by state, so `felis` scans recent jobs in pages of 100 and stops once it has `--limit` matches or has scanned 1,000 jobs.
  - `--all` includes finished jobs: the `--limit` most recent jobs in any state, from a single request.
- `job status` prints the latest event and the event history.
- `job result` prints counts for a finished job. With `--wait` it blocks until the job finishes, as `run` does.
- `job cancel` cancels a job.

### API commands

Each API command maps to exactly one endpoint. JSON responses are printed as JSON; the file downloads print their raw content.

| Command | Endpoint |
|---|---|
| `felis api health` | `GET v1/health/` |
| `felis api list-targets` | `GET v1/targets/` |
| `felis api target-health TARGET` | `GET v1/targets/{target}/health` |
| `felis api target-availabilities TARGET [--limit N] [--offset N]` | `GET v1/targets/{target}/availabilities` |
| `felis api list-jobs [--page N] [--limit N]` | `GET v1/jobs/` |
| `felis api create-job TARGET [--param k=v ...]` | `POST v1/jobs/` |
| `felis api upload-input JOB_ID FILE.ll` | `POST v1/jobs/{id}/input` |
| `felis api get-job JOB_ID` | `GET v1/jobs/{id}` |
| `felis api cancel-job JOB_ID` | `DELETE v1/jobs/{id}` |
| `felis api get-metrics JOB_ID` | `GET v1/jobs/{id}/metrics` |
| `felis api download-input JOB_ID` | `GET v1/jobs/{id}/input` |
| `felis api download-transpiled JOB_ID` | `GET v1/jobs/{id}/transpiled` |
| `felis api download-output JOB_ID` | `GET v1/jobs/{id}/output` |
| `felis api download-memory JOB_ID` | `GET v1/jobs/{id}/memory` |

Where the provider has a function for an endpoint (in `qiskit_alice_bob_provider.remote.api.jobs` and `.targets`), the command uses it. The others (`health`, `target-health`, `target-availabilities`, `list-jobs`) call `ApiClient.get` directly.

### Global flags

`--api-key`, `--endpoint`, `--debug` (show full tracebacks).

## Circuit file convention

A circuit file is a Python file that defines a top-level variable `circuit` of type `qiskit.QuantumCircuit`. `felis` loads it with `runpy.run_path` and reads that variable. A missing or wrongly-typed `circuit` is an error that names the file. `cli/circuit.py` holds the module-2 example, which prepares `+` and measures in the X basis.

## Script structure

Each section below is a group of functions in the script:

1. **Config:** `load_config(args) -> Config(api_key, endpoint)`.
2. **Client:** `make_client(config) -> ApiClient`, created lazily by each command that needs it.
3. **Circuits:**
   - `load_circuit(path) -> QuantumCircuit`.
   - `to_qir(circuit, target_name, client) -> str`:
     - Finds the target description in `list_targets(client)` and builds an `AliceBobRemoteBackend(client, description)`.
     - Runs `transpile`, then `PassManager([EnsurePreparationPass()])`, then `circuit_to_qir`.
     - Does not construct `AliceBobRemoteProvider`, which avoids its PyPI version check.
     - An unknown target is an error that lists the valid target names.
4. **Jobs:**
   - `submit(client, target, qir, params) -> job`: calls `create_job`, then `upload_input`.
   - `wait(client, job_id, timeout, on_event) -> job`:
     - Polls `get_job` once a second and calls `on_event` for each new event.
     - Returns the job once its last event is a terminal one: `SUCCEEDED`, `*_FAILED`, `CANCELLED` or `TIMED_OUT`.
     - Raises `WaitTimeout` after `timeout` seconds. The default is no timeout.
   - `counts(client, job_id) -> dict[str, int]`:
     - Parses `download_output`, which is CSV rows of `<bitstring>,<count>`.
     - Keys are the bitstrings exactly as the API returns them (e.g. `"1"`, `"0"`). They are not converted to hex the way Qiskit's counts are.
5. **Commands:** one `cmd_<name>(args)` function per command.
6. **CLI:** `build_parser()` builds the argparse tree, with `api` as a nested subparser. `main()` dispatches to the commands and maps errors to exit codes.

## Parameters

- `--param k=v` sets `inputParams[k]`. The value is parsed as JSON, and kept as a string if it doesn't parse.
- `--shots N` is shorthand for `--param nbShots=N`.
- Parameters are passed through to the API unchanged. The API's 422 error is reported if a parameter is invalid.

## Output, errors and exit codes

- Top-level commands print readable text by default; `--json` prints the underlying JSON instead.
- API commands always print JSON, or raw file content for the downloads.
- Status and progress messages go to stderr. Data (QIR, JSON, counts) goes to stdout, so `felis generate … > x.ll` and `felis … --json | jq` work.

| Exit | Meaning |
|---|---|
| 0 | Success. |
| 1 | Usage, config or API error. An `AliceBobApiException` is shown as one line: `felis: HTTP <code>: <message>`. |
| 2 | The job ended unsuccessfully: failed, cancelled, or timed out on the server. The job's `errors` are printed. |
| 3 | The local `--timeout` expired. The job is still running, and its ID is printed. |
| 130 | Interrupted. |

- **Ctrl-C during a blocking `run` or `job result --wait`:** on a TTY, `felis` asks whether to cancel the job. Otherwise it leaves the job running and prints its ID.
- **`--debug`:** shows the full traceback for every error.

## Testing

`cli/test_felis.py` uses pytest. Each test loads the script as a module with `importlib`, because the script has no `.py` extension, and replaces `ApiClient` with a fake:

- config precedence and the missing-key error;
- `--param` parsing (JSON and string fallback, plus the `--shots` shorthand);
- `load_circuit`: the valid case, a missing variable, and the wrong type;
- `wait`: success, each failure type, the local timeout, and event de-duplication;
- every API command calls the expected endpoint with the expected arguments;
- `run` with `.ll` and with `.py` input submits the expected QIR. For the `.py` case, `to_qir` is stubbed.

### Manual smoke test (production, `EMU:1Q:LESCANNE_2020`)

1. `felis generate EMU:1Q:LESCANNE_2020 circuit.py` produces the same output as `hands-on/module-2/alice-bob/generated_circuit.ll`.
2. `felis run EMU:1Q:LESCANNE_2020 circuit.py` and `felis run … generated_circuit.ll` both return counts.
3. Each `felis api …` command runs once against a real job.
