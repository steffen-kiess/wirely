"""Prepare native configuration during compilation."""

from wirely.native.csv import CsvWriterParameters
from wirely.native_configuration import register_configuration


def prepare_csv_writer_parameters(*, context):
    parameters = context.parameters
    ty = parameters['Type']
    if not ty.supports_csv:
        raise context.error(f'Type {ty} does not support converting to CSV', parameter='Type')

    filename = parameters['Filename']

    return CsvWriterParameters(type=ty, filename=filename)


register_configuration(CsvWriterParameters, prepare_csv_writer_parameters)
