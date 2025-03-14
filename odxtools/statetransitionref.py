# SPDX-License-Identifier: MIT
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree

from .basicstructure import BasicStructure
from .dataobjectproperty import DataObjectProperty
from .exceptions import odxassert, odxraise, odxrequire
from .odxlink import OdxDocFragment, OdxLinkDatabase, OdxLinkId, OdxLinkRef, resolve_snref
from .odxtypes import ParameterValue, ParameterValueDict
from .parameters.codedconstparameter import CodedConstParameter
from .parameters.parameter import Parameter
from .parameters.physicalconstantparameter import PhysicalConstantParameter
from .parameters.tablekeyparameter import TableKeyParameter
from .parameters.tablestructparameter import TableStructParameter
from .parameters.valueparameter import ValueParameter
from .snrefcontext import SnRefContext
from .statemachine import StateMachine
from .statetransition import StateTransition
from .utils import dataclass_fields_asdict

if TYPE_CHECKING:
    from .statemachine import StateMachine
    from .tablerow import TableRow


def _resolve_in_param(
    in_param_if_snref: Optional[str],
    in_param_if_snpathref: Optional[str],
    params: List[Parameter],
    param_dict: ParameterValueDict,
) -> Tuple[Optional[Parameter], Optional[ParameterValue]]:

    if in_param_if_snref is not None:
        path_chunks = [in_param_if_snref]
    elif in_param_if_snpathref is not None:
        path_chunks = in_param_if_snpathref.split(".")
    else:
        return None, None

    return _resolve_in_param_helper(params, param_dict, path_chunks)


def _resolve_in_param_helper(
    params: List[Parameter],
    param_dict: ParameterValueDict,
    path_chunks: List[str],
) -> Tuple[Optional[Parameter], Optional[ParameterValue]]:

    inner_param = resolve_snref(path_chunks[0], params, Parameter)
    inner_param_value = param_dict.get(path_chunks[0])
    if inner_param is None:
        return None, None

    if len(path_chunks) == 1:
        return inner_param, inner_param_value

    # deal with table parameters
    if isinstance(inner_param, TableStructParameter):
        if not isinstance(inner_param_value, tuple) or len(inner_param_value) == 2:
            odxraise("Invalid value for table struct parameter")
            return None, None

        if TYPE_CHECKING:
            tr = resolve_snref(inner_param_value[0], inner_param.table_key.table.table_rows,
                               TableRow)
        else:
            tr = resolve_snref(inner_param_value[0], inner_param.table_key.table.table_rows)

        if tr.structure is None:
            odxraise("Table row must reference a structure")
            return None, None

        inner_param_list = tr.structure.parameters
        inner_param_value = inner_param_value[1]
    else:
        dop = getattr(inner_param, "dop", None)
        if isinstance(dop, BasicStructure):
            inner_param_list = dop.parameters
        else:
            odxraise(f"Expected the referenced parameter to be composite")

    if not isinstance(inner_param_value, dict):
        odxraise(f"Expected the referenced parameter to be composite")
        return None, None

    return _resolve_in_param_helper(inner_param_list, inner_param_value, path_chunks[1:])


@dataclass
class StateTransitionRef(OdxLinkRef):
    """Describes a state transition that is to be potentially taken if
    a diagnostic communication is executed

    Besides the "raw" state transistion, state transition references
    may also be conditional on the observed response of the ECU.

    """
    value: Optional[str]

    in_param_if_snref: Optional[str]
    in_param_if_snpathref: Optional[str]

    @property
    def state_transition(self) -> StateTransition:
        return self._state_transition

    @staticmethod
    def from_et(  # type: ignore[override]
            et_element: ElementTree.Element,
            doc_frags: List[OdxDocFragment]) -> "StateTransitionRef":
        kwargs = dataclass_fields_asdict(OdxLinkRef.from_et(et_element, doc_frags))

        value = et_element.findtext("VALUE")

        in_param_if_snref = None
        if (in_param_if_snref_elem := et_element.find("IN-PARAM-IF-SNREF")) is not None:
            in_param_if_snref = odxrequire(in_param_if_snref_elem.get("SHORT-NAME"))

        in_param_if_snpathref = None
        if (in_param_if_snpathref_elem := et_element.find("IN-PARAM-IF-SNPATHREF")) is not None:
            in_param_if_snpathref = odxrequire(in_param_if_snpathref_elem.get("SHORT-NAME-PATH"))

        return StateTransitionRef(
            value=value,
            in_param_if_snref=in_param_if_snref,
            in_param_if_snpathref=in_param_if_snpathref,
            **kwargs)

    def __post_init__(self) -> None:
        if self.value is not None:
            odxassert(self.in_param_if_snref is not None or self.in_param_if_snref is not None,
                      "If VALUE is specified, a parameter must be referenced")

    def _build_odxlinks(self) -> Dict[OdxLinkId, Any]:
        return {}

    def _resolve_odxlinks(self, odxlinks: OdxLinkDatabase) -> None:
        self._state_transition = odxlinks.resolve(self, StateTransition)

    def _resolve_snrefs(self, context: SnRefContext) -> None:
        pass

    def execute(self, state_machine: StateMachine, params: List[Parameter],
                param_value_dict: ParameterValueDict) -> bool:
        """Update a StateMachine object if the state transition ought
        to be executed based on the response received after executing a
        diagnostic communication

        Note that the specification is unclear about what the
        parameters are: It says "The optional VALUE together with the
        also optional IN-PARAM-IF snref at STATE-TRANSITION-REF and
        PRE-CONDITION-STATE-REF can be used if the STATE-TRANSITIONs
        and pre-condition STATEs are dependent on the values of the
        referenced PARAMs.", but it does not specify what the
        "referenced PARAMs" are. For the state transition refs, let's
        assume that they are the parameters of a response received
        from the ECU, whilst we assume that they are the parameters of
        the request for PRE-CONDITION-STATE-REFs.

        """

        # check if the finite state machine is in the referenced source state
        st = self.state_transition
        if st.source_state != state_machine.active_state:
            return False

        if self.in_param_if_snref is None and self.in_param_if_snpathref is None:
            # if there is no dependence on a parameter, take the state
            # transition
            state_machine.active_state = st.target_state
            return True

        # retrieve the referenced parameter and compare it to the
        # requisite value
        param, param_value = _resolve_in_param(self.in_param_if_snref, self.in_param_if_snpathref,
                                               params, param_value_dict)

        if param is None:
            # The referenced parameter does not exist.
            return False
        elif not isinstance(
                param,
            (CodedConstParameter, PhysicalConstantParameter, TableKeyParameter, ValueParameter)):
            # see checker rule 194 in section B.2 of the spec
            odxraise(f"Parameter referenced by state transition ref is of "
                     f"invalid type {type(param).__name__}")
            return False
        elif isinstance(param, (CodedConstParameter, PhysicalConstantParameter,
                                TableKeyParameter)) and self.value is not None:
            # see checker rule 193 in section B.2 of the spec. Why can
            # no values for constant parameters be specified? (This
            # seems to be rather inconvenient...)
            odxraise(f"No value may be specified for state transitions referencing "
                     f"parameters of type {type(param).__name__}")
            return False
        elif isinstance(param, ValueParameter):
            # see checker rule 193 in section B.2 of the spec
            if self.value is None:
                odxraise(f"An expected VALUE must be specified for state "
                         f"transitions referencing VALUE parameters")
                return False

            if param_value is None:
                param_value = param.physical_default_value
                if param_value is None:
                    odxraise(f"No value can be determined for parameter {param.short_name}")
                    return False

            if param.dop is None or not isinstance(param.dop, DataObjectProperty):
                odxraise(f"Non-simple DOP specified for parameter {param.short_name}")
                return False

            expected_value = param.dop.physical_type.base_data_type.from_string(self.value)

            if expected_value != param_value:
                return False

        state_machine.active_state = st.target_state
        return True
