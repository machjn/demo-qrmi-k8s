# Alice and Bob Felis QRMI - Examples in Python

## Prerequisites

* Python 3.11 or 3.12
* A Felis API key. See [onboarding](../../../onboarding/alice-bob.md)

Warning: Do not work in the same venv you were using for other QRMI vendors. 

## Install dependencies

```shell-session
cd hands-on/module-2/alice-bob/
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

This installs `qrmi` from PyPI with the `alice-bob` extra, which pulls in [qiskit-alice-bob-provider](https://github.com/Alice-Bob-SW/qiskit-alice-bob-provider).

## Set environment variables

QRMI is configured through environment variables. Copy the example file and fill in your API key:

```shell-session
cp .env.example .env
```

## Optional: Generate QIR input file

You will find some sample circuits in [samples/](./samples/)

Felis accepts circuits expressed in QIR, a language which expresses intermediate representations of quantum circuits.

You can modify a Qiskit circuit in [`generate_input.py`](./generate_input.py) before generating it in QIR form as follows:

```shell-session
python generate_input.py EMU:1Q:LESCANNE_2020 > generated_circuit.ll
```

## Run

For example:

```shell-session
python example.py ab_emu_1q_lescanne_2020 generated_circuit.ll
```


