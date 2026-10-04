"""Compiler-facing AST, graph, parameter, and diagnostic objects."""

import importlib

from wirely import (
    Some, Unit, MonotonicTime, Duration, TimeSnapshot, RunStage,
    types, value_types, Type, ValueType, NativeNodeContext, NativeState,
    Variable, VariableBlock, VariableBlockInstance, InputSourceBinding,
    ProcedureContext, StepContext, Program, Engine,
)


_exports = {
    'ResourceType': 'compiler.types',
    'TypeParameter': 'compiler.types',
    'Value': 'compiler.value',
    'ConstantValue': 'compiler.value',
    'ValueParameter': 'compiler.value',
    'ParameterValues': 'compiler.parameters',
    'NativeNodeConfigurationContext': 'compiler.native_configuration',
    'Node': 'compiler.node',
    'NodeInstance': 'compiler.node',
    'BlockInput': 'compiler.node',
    'BlockOutput': 'compiler.node',
    'BlockTemporaryVariable': 'compiler.node',
    'BlockStateVariable': 'compiler.node',
    'StateRead': 'compiler.node',
    'Connection': 'compiler.node',
    'Block': 'compiler.block',
    'BlockInstance': 'compiler.block',
    'connect': 'compiler.support',
    'path_resolve_singleton': 'compiler.support',
    'resolve_variable': 'compiler.support',
    'StaticBlockContext': 'compiler.support',
    'StaticProcedureContext': 'compiler.support',
    'UnitLiteral': 'compiler.ast',
    'ProcedureBinding': 'compiler.ast',
    'UsesDeclaration': 'compiler.ast',
    'StaticBinding': 'compiler.ast',
    'DeclarationInitializer': 'compiler.ast',
    'ProgramError': 'compiler.ast',
    'InternalCompilerError': 'compiler.ast',
    'CompilerHint': 'compiler.ast',
    'SourcePosition': 'compiler.ast',
    'Declaration': 'compiler.ast',
    'BlockConnectorTypeSpecifier': 'compiler.ast',
    'ListSpecifier': 'compiler.ast',
    'Identifier': 'compiler.ast',
    'IntNumber': 'compiler.ast',
    'FloatNumber': 'compiler.ast',
    'StringConstant': 'compiler.ast',
    'Reference': 'compiler.ast',
    'DeclarationConnector': 'compiler.ast',
    'DeclarationParameter': 'compiler.ast',
    'DeclarationConstant': 'compiler.ast',
    'DeclarationAssertion': 'compiler.ast',
    'BlockInstantiation': 'compiler.ast',
    'DeclarationBlockInstantiation': 'compiler.ast',
    'DeclarationConnection': 'compiler.ast',
    'DeclarationStateVariable': 'compiler.ast',
    'DeclarationTemporaryVariable': 'compiler.ast',
    'DeclarationProcedure': 'compiler.ast',
    'NativeSpecifier': 'compiler.ast',
    'OptionalSpecifier': 'compiler.ast',
    'StageDeclaration': 'compiler.ast',
    'DependsDeclaration': 'compiler.ast',
    'ProducesDeclaration': 'compiler.ast',
    'ConnectionKindSpecifier': 'compiler.ast',
    'ConnectorSpecification': 'compiler.ast',
    'ConnectorSpecificationData': 'compiler.ast',
    'ConnectorSpecificationResource': 'compiler.ast',
    'DeclarationResourceType': 'compiler.ast',
    'DeclarationEnumType': 'compiler.ast',
    'EnumMember': 'compiler.ast',
    'DeclarationStructType': 'compiler.ast',
    'StructField': 'compiler.ast',
    'StructLiteral': 'compiler.ast',
    'StructLiteralField': 'compiler.ast',
    'DeclarationLibrary': 'compiler.ast',
    'BlockParameter': 'compiler.ast',
    'Scope': 'compiler.ast',
    'BlockTypeSpecifier': 'compiler.ast',
    'ListLiteral': 'compiler.ast',
    'DeclarationNativeFunction': 'compiler.ast',
    'FunctionArgument': 'compiler.ast',
    'FunctionCall': 'compiler.ast',
    'BracketOperator': 'compiler.ast',
    'ConnectionKind': 'compiler.ast',
    'BlockType': 'compiler.block_type',
}
_modules = {
    'ast': 'compiler.ast',
    'block_type': 'compiler.block_type',
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
