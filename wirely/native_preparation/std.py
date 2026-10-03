"""Prepare native configuration during compilation."""

from wirely.native.std import EdgeData
from wirely.native_configuration import register_configuration


def prepare_edge_data(*, context):
    parameters = context.parameters
    par_edge_type = parameters['EdgeType']

    if par_edge_type not in ['Rising', 'Falling', 'Both']:
        raise context.error(f'Invalid edge type: {par_edge_type!r}', parameter='EdgeType')

    return EdgeData(edge_type=par_edge_type)


register_configuration(EdgeData, prepare_edge_data)
