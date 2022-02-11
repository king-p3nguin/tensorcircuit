"""
Quantum circuit class but with density matrix simulator
"""
# pylint: disable=invalid-name

from functools import reduce
from operator import add
from typing import Any, Callable, List, Optional, Sequence, Tuple

import numpy as np
import tensornetwork as tn

from . import gates
from . import channels
from .circuit import Circuit
from .cons import backend, contractor, npdtype

Gate = gates.Gate
Tensor = Any


class DMCircuit:
    def __init__(
        self,
        nqubits: int,
        empty: bool = False,
        inputs: Optional[Tensor] = None,
        dminputs: Optional[Tensor] = None,
    ) -> None:
        if not empty:
            _prefix = "qb-"
            if (inputs is None) and (dminputs is None):
                # Get nodes on the interior
                nodes = [
                    tn.Node(
                        np.array(
                            [
                                1.0,
                                0.0,
                            ],
                            dtype=npdtype,
                        ),
                        name=_prefix + str(x + 1),
                    )
                    for x in range(nqubits)
                ]
                self._rfront = [n.get_edge(0) for n in nodes]

                lnodes, self._lfront = self._copy(nodes, self._rfront, conj=True)
                lnodes.extend(nodes)
                self._nodes = lnodes
            elif inputs is not None:
                inputs = backend.reshape(inputs, [-1])
                N = inputs.shape[0]
                n = int(np.log(N) / np.log(2))
                assert n == nqubits
                inputs = backend.reshape(inputs, [2 for _ in range(n)])
                inputs = Gate(inputs)
                nodes = [inputs]
                self._rfront = [inputs.get_edge(i) for i in range(n)]

                lnodes, self._lfront = self._copy(nodes, self._rfront, conj=True)
                lnodes.extend(nodes)
                self._nodes = lnodes
            else:  # dminputs is not None
                dminputs = backend.reshape(dminputs, [2 for _ in range(2 * nqubits)])
                dminputs = Gate(dminputs)
                nodes = [dminputs]
                self._rfront = [dminputs.get_edge(i) for i in range(nqubits)]
                self._lfront = [dminputs.get_edge(i + nqubits) for i in range(nqubits)]
                self._nodes = nodes

            self._nqubits = nqubits

    @classmethod
    def _meta_apply(cls) -> None:
        for g in Circuit.sgates:
            setattr(cls, g, cls.apply_general_gate_delayed(getattr(gates, g), name=g))
            setattr(
                cls,
                g.upper(),
                cls.apply_general_gate_delayed(getattr(gates, g), name=g),
            )

        for g in Circuit.vgates:
            setattr(
                cls,
                g,
                cls.apply_general_variable_gate_delayed(getattr(gates, g), name=g),
            )
            setattr(
                cls,
                g.upper(),
                cls.apply_general_variable_gate_delayed(getattr(gates, g), name=g),
            )

        for k in channels.channels:
            setattr(
                cls,
                k,
                cls.apply_general_kraus_delayed(getattr(channels, k + "channel")),
            )

    def _copy(
        self,
        nodes: Sequence[tn.Node],
        dangling: Optional[Sequence[tn.Edge]] = None,
        conj: Optional[bool] = False,
    ) -> Tuple[List[tn.Node], List[tn.Edge]]:
        """
        copy all nodes and dangling edges correspondingly

        :return:
        """
        ndict, edict = tn.copy(nodes, conjugate=conj)
        newnodes = []
        for n in nodes:
            newnodes.append(ndict[n])
        newfront = []
        if not dangling:
            dangling = []
            for n in nodes:
                dangling.extend([e for e in n])
        for e in dangling:
            newfront.append(edict[e])
        return newnodes, newfront

    def _copy_DMCircuit(self) -> "DMCircuit":
        newnodes, newfront = self._copy(self._nodes, self._lfront + self._rfront)
        newDMCircuit = DMCircuit(self._nqubits, empty=True)
        newDMCircuit._nqubits = self._nqubits
        newDMCircuit._lfront = newfront[: self._nqubits]
        newDMCircuit._rfront = newfront[self._nqubits :]
        newDMCircuit._nodes = newnodes
        return newDMCircuit

    def _copy_dm_tensor(
        self, conj: bool = False, reuse: bool = True
    ) -> Tuple[List[tn.Node], List[tn.Edge]]:
        if reuse:
            t = getattr(self, "state_tensor", None)
        else:
            t = None
        if t is None:
            nodes, d_edges = self._copy(
                self._nodes, self._rfront + self._lfront, conj=conj
            )
            t = contractor(nodes, output_edge_order=d_edges)
            setattr(self, "state_tensor", t)
        ndict, edict = tn.copy([t], conjugate=conj)
        newnodes = []
        newnodes.append(ndict[t])
        newfront = []
        for e in t.edges:
            newfront.append(edict[e])
        return newnodes, newfront

    def _contract(self) -> None:
        t = contractor(self._nodes, output_edge_order=self._rfront + self._lfront)
        self._nodes = [t]

    def apply_general_gate(
        self, gate: Gate, *index: int, name: Optional[str] = None
    ) -> None:
        assert len(index) == len(set(index))
        noe = len(index)
        lgated, _ = self._copy([gate], conj=True)
        lgate = lgated[0]
        for i, ind in enumerate(index):
            gate.get_edge(i + noe) ^ self._rfront[ind]
            self._rfront[ind] = gate.get_edge(i)
            lgate.get_edge(i + noe) ^ self._lfront[ind]
            self._lfront[ind] = lgate.get_edge(i)
        self._nodes.append(gate)
        self._nodes.append(lgate)
        setattr(self, "state_tensor", None)

    @staticmethod
    def apply_general_gate_delayed(
        gatef: Callable[[], Gate], name: Optional[str] = None
    ) -> Callable[..., None]:
        def apply(self: "DMCircuit", *index: int) -> None:
            gate = gatef()
            self.apply_general_gate(gate, *index, name=name)

        return apply

    @staticmethod
    def apply_general_variable_gate_delayed(
        gatef: Callable[..., Gate],
        name: Optional[str] = None,
    ) -> Callable[..., None]:
        def apply(self: "DMCircuit", *index: int, **vars: float) -> None:
            gate = gatef(**vars)
            self.apply_general_gate(gate, *index, name=name)

        return apply

    @staticmethod
    def check_kraus(kraus: Sequence[Gate]) -> bool:  # TODO(@refraction-ray)
        return True

    def apply_general_kraus(
        self, kraus: Sequence[Gate], index: Sequence[Tuple[int, ...]]
    ) -> None:
        # note the API difference for index arg between DM and DM2
        self.check_kraus(kraus)
        assert len(kraus) == len(index) or len(index) == 1
        if len(index) == 1:
            index = [index[0] for _ in range(len(kraus))]
        self._contract()
        circuits = []
        for k, i in zip(kraus, index):
            dmc = self._copy_DMCircuit()
            dmc.apply_general_gate(k, *i)
            dd = dmc.densitymatrix()
            circuits.append(dd)
        tensor = reduce(add, circuits)
        tensor = backend.reshape(tensor, [2 for _ in range(2 * self._nqubits)])
        self._nodes = [Gate(tensor)]
        dangling = [e for e in self._nodes[0]]
        self._rfront = dangling[: self._nqubits]
        self._lfront = dangling[self._nqubits :]
        setattr(self, "state_tensor", None)

    general_kraus = apply_general_kraus

    @staticmethod
    def apply_general_kraus_delayed(
        krausf: Callable[..., Sequence[Gate]]
    ) -> Callable[..., None]:
        def apply(self: "DMCircuit", *index: int, **vars: float) -> None:
            kraus = krausf(**vars)
            self.apply_general_kraus(kraus, [index])

        return apply

    def densitymatrix(self, check: bool = False, reuse: bool = True) -> tn.Node.tensor:
        nodes, _ = self._copy_dm_tensor(conj=False, reuse=reuse)
        # t = contractor(nodes, output_edge_order=d_edges)
        dm = backend.reshape(
            nodes[0].tensor, shape=[2 ** self._nqubits, 2 ** self._nqubits]
        )
        if check:
            self.check_density_matrix(dm)
        return dm

    state = densitymatrix

    def expectation(self, *ops: Tuple[tn.Node, List[int]]) -> tn.Node.tensor:
        newdm, newdang = self._copy(self._nodes, self._rfront + self._lfront)
        occupied = set()
        nodes = newdm
        for op, index in ops:
            noe = len(index)
            for j, e in enumerate(index):
                if e in occupied:
                    raise ValueError("Cannot measure two operators in one index")
                newdang[e + self._nqubits] ^ op.get_edge(j)
                newdang[e] ^ op.get_edge(j + noe)
                occupied.add(e)
            nodes.append(op)
        for j in range(self._nqubits):
            if j not in occupied:  # edge1[j].is_dangling invalid here!
                newdang[j] ^ newdang[j + self._nqubits]
        return contractor(nodes).tensor

    @staticmethod
    def check_density_matrix(dm: Tensor) -> None:
        assert np.allclose(backend.trace(dm), 1.0, atol=1e-5)


DMCircuit._meta_apply()
