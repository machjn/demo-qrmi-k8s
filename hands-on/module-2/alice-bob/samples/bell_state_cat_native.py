"""Bell state, the cat-qubit way: prepare |+> directly, then a CNOT.

Same result as bell_state.py, only `00` and `11`, but without a Hadamard gate.
Cat qubits can prepare |+> natively, which is what the Hadamard does to |0>, so
this also runs on the physical-cat emulators, which have no Hadamard gate:

    ./felis run EMU:6Q:PHYSICAL_CATS samples/bell_state_cat_native.py --shots 100

On a noisy target like this one, an occasional `01` or `10` is possible.
"""

from qiskit import QuantumCircuit

circuit = QuantumCircuit(2, 2)
circuit.initialize("+", 0)  # prepare qubit 0 in |+>, natively
circuit.cx(0, 1) # CNOT entangles
circuit.measure([0, 1], [0, 1])
