"""Bell state, the textbook way: a Hadamard gate, then a CNOT.

The two results always agree, so you should see only `00` and `11`, each about
half the time. Needs a target with at least two qubits and a Hadamard gate,
such as the logical-qubit emulators:

    ./felis run EMU:40Q:LOGICAL_NOISELESS samples/bell_state.py --shots 100

The physical-cat emulators have no Hadamard gate, so this circuit won't
transpile for them; see bell_state_cat_native.py for a version that runs there.
"""

from qiskit import QuantumCircuit

circuit = QuantumCircuit(2, 2)
circuit.h(0)  # put qubit 0 in an equal superposition of 0 and 1.
circuit.cx(0, 1) # CNOT
circuit.measure([0, 1], [0, 1])
