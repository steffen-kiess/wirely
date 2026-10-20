"""Describe native interfaces declared in a compiled graph with wirely.runtime.signatures."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, overload

from wirely import types
from wirely.compiler import model
from wirely.compiler.ast.specifiers import ConnectionKind
from wirely.compiler.expressions import compile_constant
from wirely.compiler.graph import port_specification
from wirely.compiler.types import TypeParameter
from wirely.runtime import signatures


def node_types(scope: model.Scope) -> Iterator[model.NodeType]:
    """Yield the node types declared in a scope, its namespaces, and its node types."""
    for node_type in scope.node_types.values():
        yield node_type
        yield from node_types(node_type)
    for library in getattr(scope, 'libraries', {}).values():
        yield from node_types(library)


def native_functions(scope: model.Scope, constant: bool) -> Iterator[model.CompiledFunction]:
    """Yield the runtime or constant native functions declared in a scope and its namespaces."""
    for function in scope.native_functions.values():
        if function.is_constant == constant:
            yield function
    for library in getattr(scope, 'libraries', {}).values():
        yield from native_functions(library, constant)


def function_signatures(graph: model.Scope, *, constant: bool,
                        namespace: str | None = None, source_paths: set[str] | None = None) -> dict[str, signatures.Function]:
    """Return the signatures of the runtime or constant native functions in a graph, by native name.

    With a namespace, only functions whose native names are in it are described.
    Raises ValueError for functions that cannot be described.
    """
    functions = {}
    for function in native_functions(graph, constant):
        if source_paths is not None and function.declaration.source_position.filename not in source_paths:
            continue
        if namespace is not None and not function.native_name.startswith(namespace + '.'):
            continue
        where = f'Native function {function.native_name}'
        function.resolve_signature()
        functions[function.native_name] = signatures.Function(
            tuple(signature_type(value_type, {}, where) for value_type in function.parameter_types),
            signature_type(function.return_type, {}, where))
    return functions


def signature_type(value_type: types.Type, variables: Mapping[model.NodeParameter, str],
                   where: str) -> signatures.SignatureType:
    """Describe a compiler value type; type parameters become their type variables."""
    if isinstance(value_type, TypeParameter):
        name = variables.get(value_type.parameter)
        if name is None:
            raise ValueError(f'{where} does not pass type {value_type.parameter.name.id_str} in uses')
        return signatures.TypeVar(name)
    if isinstance(value_type, types.TypeInt):
        return signatures.Int(value_type.bits, value_type.is_signed)
    if isinstance(value_type, types.TypeOption):
        return signatures.Option(signature_type(value_type.inner_type, variables, where))
    if isinstance(value_type, types.TypeList):
        return signatures.List(signature_type(value_type.inner_type, variables, where))
    if isinstance(value_type, types.TypeEnum):
        if value_type.native_name is None:
            raise ValueError(f'{where} uses enum {value_type}, which is not native')
        return signatures.Enum(value_type.native_name)
    if isinstance(value_type, types.TypeStruct):
        if value_type.native_name is None:
            raise ValueError(f'{where} uses struct {value_type}, which is not native')
        if value_type.is_opaque:
            return signatures.Opaque(value_type.native_name)
        return signatures.Struct(value_type.native_name)
    if str(value_type) in ('unit', 'boolean', 'String', 'type'):
        return signatures.Builtin(str(value_type))
    raise ValueError(f'{where} uses unsupported type {value_type}')


# Data and resource ports expose different interfaces.
def signature_port(port: Any, variables: Mapping[model.NodeParameter, str], where: str) -> signatures.Port:
    specification = port_specification(port)
    if specification.resource_type is not None:
        return signatures.Port('resource', signatures.ResourceType(specification.resource_type.native_name),
                               specification.is_list)
    kind_spec = getattr(specification.port_spec, 'kind_spec', None)
    kind = 'event' if kind_spec is not None and kind_spec.kind is ConnectionKind.Event else 'value'
    assert specification.var_type is not None
    return signatures.Port(kind, signature_type(specification.var_type, variables, where), specification.is_list)


def procedure_ports(ports: Mapping[str, model.ProcedureInputPort | model.ProcedureOutputPort],
                    variables: Mapping[model.NodeParameter, str], where: str) -> dict[str, signatures.Port]:
    result = {}
    for port in ports.values():
        port.resolve_reference()
        result[port.declaration.python_key] = signature_port(port, variables, where)
    return result


def type_variables(node_type: model.NodeType, uses: model.UsesDeclaration | None,
                   where: str) -> dict[model.NodeParameter, str]:
    """Map each `type` parameter in a `uses` clause to the key passing it."""
    variables: dict[model.NodeParameter, str] = {}
    for binding in () if uses is None else uses.bindings:
        expression = binding.expression
        parameter = (node_type.parsed_parameters.get(expression.identifiers[0].id_str)
                     if isinstance(expression, model.Reference) and expression.length == 1 else None)
        if parameter is not None and getattr(parameter, 'type_resolved', None) == types.ValueType:
            if variables.setdefault(parameter, binding.key) != binding.key:
                raise ValueError(f'{where} passes type {expression} under two names')
        elif uses_type(node_type, binding, None, where) == types.ValueType:
            raise ValueError(f'{where} passes a type that is not a parameter: {expression}')
    return variables


@overload
def uses_type(node_type: model.NodeType, binding: model.StaticBinding, variables: None, where: str) -> types.Type: ...

@overload
def uses_type(node_type: model.NodeType, binding: model.StaticBinding,
              variables: Mapping[model.NodeParameter, str], where: str) -> signatures.SignatureType: ...

def uses_type(node_type: model.NodeType, binding: model.StaticBinding,
              variables: Mapping[model.NodeParameter, str] | None,
              where: str) -> types.Type | signatures.SignatureType:
    with binding.expression.context:
        value_type = compile_constant(node_type, binding.expression, None, {}).value_type
    return value_type if variables is None else signature_type(value_type, variables, where)


def signature_uses(node_type: model.NodeType, uses: model.UsesDeclaration | None,
                   variables: Mapping[model.NodeParameter, str],
                   where: str) -> dict[str, signatures.SignatureType]:
    if uses is None:
        return {}
    return {binding.key: uses_type(node_type, binding, variables, where) for binding in uses.bindings}


def native_signatures(graph: model.Scope,
                      namespace: str | None = None, *, source_paths: set[str] | None = None) -> tuple[dict[str, signatures.Initializer], dict[str, signatures.Procedure], dict[str, signatures.Function]]:
    """Return the signatures of native state types, procedures, and runtime functions in a graph.

    With a namespace, only declarations whose native names are in it are described.
    Raises ValueError for interfaces that cannot be described.
    """
    def included(name: str | None) -> bool:
        assert name is not None
        return namespace is None or name.startswith(namespace + '.')

    state_types = {}
    procedures = {}
    for node_type in node_types(graph):
        if source_paths is not None and node_type.declaration.source_position.filename not in source_paths:
            continue
        initializer = node_type.initializer if node_type.is_native else None
        if initializer is not None and included(node_type.native_name):
            assert node_type.native_name is not None
            where = f'Native state type {node_type.native_name}'
            variables = type_variables(node_type, initializer.uses, where)
            depends = {}
            for port, dependency in zip(initializer.depends.dependencies,
                                        initializer.resolved_dependencies()):
                if isinstance(dependency, model.InstanceNode):
                    dependency = dependency.output_ports_byname['']
                depends[port.key] = signature_port(dependency, variables, where)
            state_types[node_type.native_name] = signatures.Initializer(
                depends=depends,
                produces={key: signature_port(output, variables, where)
                          for output, key in initializer.output_keys.items()},
                uses=signature_uses(node_type, initializer.uses, variables, where),
            )
        for procedure in node_type.procedures.values():
            if not procedure.is_native or not included(procedure.native_name):
                continue
            where = f'Native procedure {procedure.native_name}'
            variables = type_variables(node_type, procedure.uses, where)
            procedures[procedure.native_name] = signatures.Procedure(
                stage=procedure.stage.stage.name.lower(),
                depends=procedure_ports(procedure.input_ports, variables, where),
                produces=procedure_ports(procedure.output_ports, variables, where),
                uses=signature_uses(node_type, procedure.uses, variables, where),
            )
    return state_types, procedures, function_signatures(graph, constant=False, namespace=namespace, source_paths=source_paths)
