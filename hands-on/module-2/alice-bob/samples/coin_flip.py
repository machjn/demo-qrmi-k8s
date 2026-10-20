"""Quantum coin flip: one qubit in superposition, measured.

The qubit is prepared in |+>, an equal superposition of 0 and 1, then measured
in the usual (Z) basis, so you should see `0` and `1` about half the time each:

    ./felis run EMU:1Q:LESCANNE_2020 samples/coin_flip.py --shots 100

Compare with ../circuit.py, which measures the same state in the X basis
instead and gets `0` almost every time.
"""

from qiskit import QuantumCircuit

circuit = QuantumCircuit(1, 1)
circuit.initialize("+")  # prepare |+>, natively supported by cat qubits
circuit.measure(0, 0)
