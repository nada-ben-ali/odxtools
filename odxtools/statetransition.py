# SPDX-License-Identifier: MIT
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree

from .element import IdentifiableElement
from .exceptions import odxrequire
from .odxlink import OdxDocFragment, OdxLinkDatabase, OdxLinkId, resolve_snref
from .snrefcontext import SnRefContext
from .state import State
from .utils import dataclass_fields_asdict


@dataclass
class ExternalAccessMethod(IdentifiableElement):
    method: str

    @staticmethod
    def from_et(et_element: ElementTree.Element,
                doc_frags: List[OdxDocFragment]) -> "ExternalAccessMethod":
        kwargs = dataclass_fields_asdict(IdentifiableElement.from_et(et_element, doc_frags))

        method = odxrequire(et_element.findtext("METHOD"))

        return ExternalAccessMethod(method=method, **kwargs)


@dataclass
class StateTransition(IdentifiableElement):
    """
    Corresponds to STATE-TRANSITION.
    """
    source_snref: str
    target_snref: str
    external_access_method: Optional[ExternalAccessMethod]

    @property
    def source_state(self) -> State:
        return self._source_state

    @property
    def target_state(self) -> State:
        return self._target_state

    @staticmethod
    def from_et(et_element: ElementTree.Element,
                doc_frags: List[OdxDocFragment]) -> "StateTransition":

        kwargs = dataclass_fields_asdict(IdentifiableElement.from_et(et_element, doc_frags))

        source_snref_elem = odxrequire(et_element.find("SOURCE-SNREF"))
        source_snref = odxrequire(source_snref_elem.attrib["SHORT-NAME"])

        target_snref_elem = odxrequire(et_element.find("TARGET-SNREF"))
        target_snref = odxrequire(target_snref_elem.attrib["SHORT-NAME"])

        external_access_method = None
        if (eam_elem := et_element.find("EXTERNAL-ACCESS-METHOD")) is not None:
            external_access_method = ExternalAccessMethod.from_et(eam_elem, doc_frags)
        return StateTransition(
            source_snref=source_snref,
            target_snref=target_snref,
            external_access_method=external_access_method,
            **kwargs)

    def _build_odxlinks(self) -> Dict[OdxLinkId, Any]:
        return {self.odx_id: self}

    def _resolve_odxlinks(self, odxlinks: OdxLinkDatabase) -> None:
        pass

    def _resolve_snrefs(self, context: SnRefContext) -> None:
        states = odxrequire(context.state_chart).states
        self._source_state = resolve_snref(self.source_snref, states, State)
        self._target_state = resolve_snref(self.target_snref, states, State)
