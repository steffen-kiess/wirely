"""Compiler-facing AST, graph, parameter, and diagnostic objects."""

import importlib

from wirely import (
    Some, Unit, MonotonicTime, Duration, TimeSnapshot, RunStage,
    enum, types, value_types, Type, ValueType, NativeNodeContext, NativeState,
    Variable, VariableBlock, VariableBlockInstance, InputSourceBinding,
    ProcedureContext, StepContext, Program, Engine,
)


_exports = {
    'ResourceType': 'compiler_types',
    'TypeParameter': 'compiler_types',
    'Value': 'value',
    'ConstantValue': 'value',
    'ValueParameter': 'value',
    'ParameterValues': 'parameters',
    'NativeNodeConfigurationContext': 'native_configuration',
    'Node': 'node',
    'NodeInstance': 'node',
    'BlockInput': 'node',
    'BlockOutput': 'node',
    'BlockTemporaryVariable': 'node',
    'BlockStateVariable': 'node',
    'StateRead': 'node',
    'Block': 'block',
    'BlockInstance': 'block',
    'connect': 'compiler_support',
    'path_resolve_singleton': 'compiler_support',
    'resolve_variable': 'compiler_support',
    'StaticBlockContext': 'compiler_support',
    'StaticProcedureContext': 'compiler_support',
    'UnitLiteral': 'ast',
    'ProcedureBinding': 'ast',
    'DeclarationInitializer': 'ast',
    'ProgramError': 'ast',
    'InternalCompilerError': 'ast',
    'CompilerHint': 'ast',
    'SourcePosition': 'ast',
    'Declaration': 'ast',
    'BlockConnectorTypeSpecifier': 'ast',
    'ListSpecifier': 'ast',
    'Identifier': 'ast',
    'IntNumber': 'ast',
    'FloatNumber': 'ast',
    'StringConstant': 'ast',
    'Reference': 'ast',
    'DeclarationConnector': 'ast',
    'DeclarationParameter': 'ast',
    'BlockInstantiation': 'ast',
    'DeclarationBlockInstantiation': 'ast',
    'DeclarationConnection': 'ast',
    'DeclarationStateVariable': 'ast',
    'DeclarationTemporaryVariable': 'ast',
    'DeclarationProcedure': 'ast',
    'NativeSpecifier': 'ast',
    'OptionalSpecifier': 'ast',
    'StageDeclaration': 'ast',
    'DependsDeclaration': 'ast',
    'ProducesDeclaration': 'ast',
    'ConnectionKindSpecifier': 'ast',
    'ConnectorSpecification': 'ast',
    'ConnectorSpecificationData': 'ast',
    'ConnectorSpecificationResource': 'ast',
    'DeclarationResourceType': 'ast',
    'DeclarationLibrary': 'ast',
    'BlockParameter': 'ast',
    'Scope': 'ast',
    'BlockTypeSpecifier': 'ast',
    'ListLiteral': 'ast',
    'FunctionCall': 'ast',
    'BracketOperator': 'ast',
    'ConnectionKind': 'ast',
    'BlockType': 'block_type',
}
_modules = {'ast', 'block_type', 'value', 'compiler', 'parser', 'util'}


def __getattr__(name):
    # Compiler modules also use this facade while defining their own objects.
    if name in _modules:
        value = importlib.import_module('.' + name, __package__)
    elif name in _exports:
        module = importlib.import_module('.' + _exports[name], __package__)
        value = getattr(module, name)
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value
