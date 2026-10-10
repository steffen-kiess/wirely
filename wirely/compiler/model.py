"""Compiler-facing AST, graph, parameter, and diagnostic objects."""

import importlib

from wirely import Some, Unit, MonotonicTime, Duration, types, value_types, Type, ValueType
from wirely.program import Program, RunStage, Variable, VariableTable, InputSource


_exports = {
    'ResourceType': 'compiler.types',
    'TypeParameter': 'compiler.types',
    'Value': 'compiler.value',
    'ConstantValue': 'compiler.value',
    'ValueParameter': 'compiler.value',
    'ParameterValues': 'compiler.parameters',
    'GraphElement': 'compiler.graph',
    'TypeInputPort': 'compiler.graph',
    'TypeOutputPort': 'compiler.graph',
    'TemporaryNode': 'compiler.graph',
    'StateNode': 'compiler.graph',
    'StateRead': 'compiler.graph',
    'PortSpecification': 'compiler.graph',
    'ProcedureInputPort': 'compiler.graph',
    'ProcedureOutputPort': 'compiler.graph',
    'Connection': 'compiler.graph',
    'InstanceNode': 'compiler.instance',
    'NodeInstance': 'compiler.instance',
    'InstanceElement': 'compiler.instance',
    'InstanceSource': 'compiler.instance',
    'InstanceTarget': 'compiler.instance',
    'InstanceInputPort': 'compiler.instance',
    'InstanceOutputPort': 'compiler.instance',
    'InstanceTemporary': 'compiler.instance',
    'PortSources': 'compiler.instance',
    'InstanceStateVariable': 'compiler.instance',
    'InstanceProcedure': 'compiler.instance',
    'NodeTypeInstance': 'compiler.instance',
    'connect': 'compiler.support',
    'connection_kind': 'compiler.support',
    'path_resolve_singleton': 'compiler.support',
    'resolve_variable': 'compiler.support',
    'traced_connections': 'compiler.support',
    'StaticNodeContext': 'compiler.support',
    'StaticProcedureContext': 'compiler.support',
    'UnitLiteral': 'compiler.ast',
    'ReferencePort': 'compiler.ast',
    'TypedPort': 'compiler.ast',
    'UsesDeclaration': 'compiler.ast',
    'StaticBinding': 'compiler.ast',
    'DeclarationInitializer': 'compiler.ast',
    'ProgramError': 'compiler.ast',
    'InternalCompilerError': 'compiler.ast',
    'CompilerHint': 'compiler.ast',
    'SourcePosition': 'compiler.ast',
    'Declaration': 'compiler.ast',
    'PortDirectionSpecifier': 'compiler.ast',
    'ListSpecifier': 'compiler.ast',
    'Identifier': 'compiler.ast',
    'IntNumber': 'compiler.ast',
    'FloatNumber': 'compiler.ast',
    'StringConstant': 'compiler.ast',
    'Reference': 'compiler.ast',
    'DeclarationPort': 'compiler.ast',
    'DeclarationParameter': 'compiler.ast',
    'DeclarationConstant': 'compiler.ast',
    'DeclarationAssertion': 'compiler.ast',
    'Instantiation': 'compiler.ast',
    'DeclarationInstance': 'compiler.ast',
    'DeclarationConnection': 'compiler.ast',
    'DeclarationState': 'compiler.ast',
    'DeclarationTemporary': 'compiler.ast',
    'DeclarationProcedure': 'compiler.ast',
    'NativeSpecifier': 'compiler.ast',
    'OptionalSpecifier': 'compiler.ast',
    'StageDeclaration': 'compiler.ast',
    'DependsDeclaration': 'compiler.ast',
    'ProducesDeclaration': 'compiler.ast',
    'ConnectionKindSpecifier': 'compiler.ast',
    'PortTypeSpecification': 'compiler.ast',
    'PortTypeSpecificationData': 'compiler.ast',
    'PortTypeSpecificationResource': 'compiler.ast',
    'DeclarationResourceType': 'compiler.ast',
    'DeclarationEnumType': 'compiler.ast',
    'EnumMember': 'compiler.ast',
    'DeclarationStructType': 'compiler.ast',
    'StructField': 'compiler.ast',
    'StructLiteral': 'compiler.ast',
    'StructLiteralField': 'compiler.ast',
    'DeclarationLibrary': 'compiler.ast',
    'ParameterBinding': 'compiler.ast',
    'Scope': 'compiler.ast',
    'NodeTypeSpecifier': 'compiler.ast',
    'ListLiteral': 'compiler.ast',
    'DeclarationNativeFunction': 'compiler.ast',
    'FunctionArgument': 'compiler.ast',
    'FunctionCall': 'compiler.ast',
    'ParameterOption': 'compiler.ast',
    'BracketOperator': 'compiler.ast',
    'ConnectionKind': 'compiler.ast',
    'NodeType': 'compiler.node_type',
}
_modules = {
    'ast': 'compiler.ast',
    'value': 'compiler.value',
    'compiler': 'compiler',
    'parser': 'compiler.parser',
    'util': 'util',
}


def __getattr__(name):
    # Compiler modules also use this facade while defining their own objects.
    if name in _modules:
        value = importlib.import_module('.' + _modules[name], 'wirely')
    elif name in _exports:
        module = importlib.import_module('.' + _exports[name], 'wirely')
        value = getattr(module, name)
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value
