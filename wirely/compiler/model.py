"""Compiler-facing AST, graph, parameter, and diagnostic objects."""

import importlib
from typing import TYPE_CHECKING, Any

from wirely import Some, Unit
from wirely.runtime import value_types
from wirely.runtime.value_types import ValueType
from wirely.program import types
from wirely.program.types import Type
from wirely.program.program import Program, RunStage, Variable, VariableTable, InputSource


# Names exported lazily; add new names to the TYPE_CHECKING imports below too.
_exports = {
    'TypeParameter': 'compiler.types',
    'Value': 'compiler.value',
    'ConstantValue': 'compiler.value',
    'ValueParameter': 'compiler.value',
    'ParameterValues': 'compiler.parameters',
    'NodeParameter': 'compiler.parameters',
    'NamespaceConstant': 'compiler.parameters',
    'GraphElement': 'compiler.graph',
    'TypeInputPort': 'compiler.graph',
    'TypeOutputPort': 'compiler.graph',
    'TemporaryNode': 'compiler.graph',
    'LocalResource': 'compiler.graph',
    'InstanceLocalResource': 'compiler.graph',
    'StateNode': 'compiler.graph',
    'StateRead': 'compiler.graph',
    'StaticSource': 'compiler.graph',
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
    'forwards_multi_input': 'compiler.support',
    'list_members': 'compiler.support',
    'path_resolve_singleton': 'compiler.support',
    'resolve_variable': 'compiler.support',
    'traced_connections': 'compiler.support',
    'StaticNodeContext': 'compiler.support',
    'StaticProcedureContext': 'compiler.support',
    'UnitLiteral': 'compiler.ast.values',
    'ReferencePort': 'compiler.ast.procedures',
    'TypedPort': 'compiler.ast.procedures',
    'StaticReads': 'compiler.ast.procedures',
    'StaticBinding': 'compiler.ast.procedures',
    'DeclarationInitializer': 'compiler.ast.procedures',
    'ProgramError': 'compiler.ast.base',
    'InternalCompilerError': 'compiler.ast.base',
    'CompilerHint': 'compiler.ast.base',
    'SourcePosition': 'compiler.ast.base',
    'Declaration': 'compiler.ast.base',
    'PortDirectionSpecifier': 'compiler.ast.specifiers',
    'ListSpecifier': 'compiler.ast.specifiers',
    'Identifier': 'compiler.ast.base',
    'IntNumber': 'compiler.ast.values',
    'FloatNumber': 'compiler.ast.values',
    'StringConstant': 'compiler.ast.values',
    'Reference': 'compiler.ast.values',
    'DeclarationPort': 'compiler.ast.ports',
    'DeclarationParameter': 'compiler.ast.parameter_declarations',
    'DeclarationConstant': 'compiler.ast.parameter_declarations',
    'DeclarationAssertion': 'compiler.ast.parameter_declarations',
    'Instantiation': 'compiler.ast.nodes',
    'DeclarationInstance': 'compiler.ast.nodes',
    'ChildNode': 'compiler.ast.nodes',
    'DeclarationConnection': 'compiler.ast.nodes',
    'DeclarationState': 'compiler.ast.nodes',
    'DeclarationBinding': 'compiler.ast.nodes',
    'DeclarationProcedure': 'compiler.ast.procedures',
    'TypeProcedure': 'compiler.ast.procedures',
    'TypeInitializer': 'compiler.ast.procedures',
    'NativeSpecifier': 'compiler.ast.specifiers',
    'OptionalSpecifier': 'compiler.ast.specifiers',
    'StageDeclaration': 'compiler.ast.procedures',
    'ReadsDeclaration': 'compiler.ast.procedures',
    'ReadPort': 'compiler.ast.procedures',
    'ReadPorts': 'compiler.ast.procedures',
    'WritesDeclaration': 'compiler.ast.procedures',
    'ConnectionKindSpecifier': 'compiler.ast.specifiers',
    'PortTypeSpecification': 'compiler.ast.specifiers',
    'PortTypeSpecificationData': 'compiler.ast.specifiers',
    'PortTypeSpecificationFixed': 'compiler.ast.specifiers',
    'DeclarationResourceType': 'compiler.ast.type_declarations',
    'DeclarationEnumType': 'compiler.ast.type_declarations',
    'EnumMember': 'compiler.ast.type_declarations',
    'DeclarationStructType': 'compiler.ast.type_declarations',
    'StructDefinition': 'compiler.ast.type_declarations',
    'EnumDefinition': 'compiler.ast.type_declarations',
    'ResourceDefinition': 'compiler.ast.type_declarations',
    'StructField': 'compiler.ast.type_declarations',
    'StructLiteral': 'compiler.ast.values',
    'StructLiteralField': 'compiler.ast.values',
    'DeclarationLibrary': 'compiler.ast.library',
    'Namespace': 'compiler.ast.library',
    'DeclarationNodeType': 'compiler.ast.node_types',
    'ParameterBinding': 'compiler.ast.nodes',
    'Scope': 'compiler.ast.scope',
    'NodeTypeSpecifier': 'compiler.ast.specifiers',
    'ListLiteral': 'compiler.ast.values',
    'DeclarationOperator': 'compiler.ast.operators',
    'DeclarationNativeFunction': 'compiler.ast.functions',
    'CompiledFunction': 'compiler.ast.functions',
    'FunctionArgument': 'compiler.ast.functions',
    'FunctionCall': 'compiler.ast.values',
    'OptionalExpression': 'compiler.ast.values',
    'BracketOperator': 'compiler.ast.values',
    'ConnectionKind': 'compiler.ast.specifiers',
    'DeclarationLoadAssertion': 'compiler.ast.procedures',
    'SourceElement': 'compiler.ast.base',
    'check_struct_literal': 'compiler.ast.values',
    'circular_connection': 'compiler.ast.nodes',
    'dependency_is_fixed': 'compiler.ast.procedures',
    'register_name': 'compiler.ast.nodes',
    'validate_nodes': 'compiler.ast.nodes',
    'NodeType': 'compiler.node_type',
}
_aliases = {
    'SyntaxValue': ('compiler.ast.base', 'Value'),
}
_modules = {
    'value': 'compiler.value',
    'parser': 'compiler.parser',
    'util': 'util',
}

if TYPE_CHECKING:
    from wirely.compiler.types import (
        TypeParameter,
    )
    from wirely.compiler.value import (
        Value,
        ConstantValue,
        ValueParameter,
    )
    from wirely.compiler.parameters import (
        ParameterValues,
        NodeParameter,
        NamespaceConstant,
    )
    from wirely.compiler.graph import (
        GraphElement,
        TypeInputPort,
        TypeOutputPort,
        TemporaryNode,
        LocalResource,
        InstanceLocalResource,
        StateNode,
        StateRead,
        StaticSource,
        PortSpecification,
        ProcedureInputPort,
        ProcedureOutputPort,
        Connection,
    )
    from wirely.compiler.instance import (
        InstanceNode,
        NodeInstance,
        InstanceElement,
        InstanceSource,
        InstanceTarget,
        InstanceInputPort,
        InstanceOutputPort,
        InstanceTemporary,
        PortSources,
        InstanceStateVariable,
        InstanceProcedure,
        NodeTypeInstance,
    )
    from wirely.compiler.support import (
        connect,
        connection_kind,
        forwards_multi_input,
        list_members,
        path_resolve_singleton,
        resolve_variable,
        traced_connections,
        StaticNodeContext,
        StaticProcedureContext,
    )
    from wirely.compiler.ast.values import (
        UnitLiteral,
        IntNumber,
        FloatNumber,
        StringConstant,
        Reference,
        StructLiteral,
        StructLiteralField,
        ListLiteral,
        FunctionCall,
        OptionalExpression,
        BracketOperator,
        check_struct_literal,
    )
    from wirely.compiler.ast.procedures import (
        ReferencePort,
        TypedPort,
        StaticReads,
        StaticBinding,
        DeclarationInitializer,
        DeclarationProcedure,
        TypeProcedure,
        TypeInitializer,
        StageDeclaration,
        ReadsDeclaration,
        ReadPort,
        ReadPorts,
        WritesDeclaration,
        DeclarationLoadAssertion,
        dependency_is_fixed,
    )
    from wirely.compiler.ast.base import (
        ProgramError,
        InternalCompilerError,
        CompilerHint,
        SourcePosition,
        Declaration,
        Identifier,
        SourceElement,
        Value as SyntaxValue,
    )
    from wirely.compiler.ast.specifiers import (
        PortDirectionSpecifier,
        ListSpecifier,
        NativeSpecifier,
        OptionalSpecifier,
        ConnectionKindSpecifier,
        PortTypeSpecification,
        PortTypeSpecificationData,
        PortTypeSpecificationFixed,
        NodeTypeSpecifier,
        ConnectionKind,
    )
    from wirely.compiler.ast.ports import DeclarationPort
    from wirely.compiler.ast.parameter_declarations import (
        DeclarationParameter,
        DeclarationConstant,
        DeclarationAssertion,
    )
    from wirely.compiler.ast.nodes import (
        Instantiation,
        DeclarationInstance,
        ChildNode,
        DeclarationConnection,
        DeclarationState,
        DeclarationBinding,
        ParameterBinding,
        circular_connection,
        register_name,
        validate_nodes,
    )
    from wirely.compiler.ast.type_declarations import (
        DeclarationResourceType,
        DeclarationEnumType,
        EnumMember,
        DeclarationStructType,
        StructDefinition,
        EnumDefinition,
        ResourceDefinition,
        StructField,
    )
    from wirely.compiler.ast.library import DeclarationLibrary, Namespace
    from wirely.compiler.ast.node_types import DeclarationNodeType
    from wirely.compiler.ast.scope import Scope
    from wirely.compiler.ast.operators import DeclarationOperator
    from wirely.compiler.ast.functions import DeclarationNativeFunction, CompiledFunction, FunctionArgument
    from wirely.compiler.node_type import (
        NodeType,
    )
    from wirely import util
    from wirely.compiler import parser, value


def __getattr__(name: str) -> Any:
    # Compiler modules also use this facade while defining their own objects.
    if name in _modules:
        value = importlib.import_module('.' + _modules[name], 'wirely')
    elif name in _aliases:
        module_name, attribute = _aliases[name]
        module = importlib.import_module('.' + module_name, 'wirely')
        value = getattr(module, attribute)
    elif name in _exports:
        module = importlib.import_module('.' + _exports[name], 'wirely')
        value = getattr(module, name)
    else:
        raise AttributeError(name)
    globals()[name] = value
    return value
