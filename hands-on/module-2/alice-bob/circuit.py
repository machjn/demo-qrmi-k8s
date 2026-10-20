"""Example circuit for `felis`: prepare |+> and measure in the X basis."""

from qiskit import QuantumCircuit

circuit = QuantumCircuit(1, 1)
circuit.initialize("+")
circuit.measure_x(0, 0)  # pylint: disable=no-member
