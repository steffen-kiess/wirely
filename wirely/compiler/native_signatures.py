"""Describe native interfaces declared in a compiled graph with wirely.runtime.signatures."""

from wirely import types
from wirely.compiler import model
from wirely.compiler.ast.specifiers import ConnectionKind
from wirely.compiler.expressions import compile_constant
from wirely.compiler.graph import port_specification
from wirely.compiler.types import TypeParameter
from wirely.runtime import signatures


def node_types(scope):
    """Yield the node types declared in a scope, its namespaces, and its node types."""
    for node_type in scope.node_types.values():
        yield node_type
        yield from node_types(node_type)
    for library in getattr(scope, 'libraries', {}).values():
        yield from node_types(library)


def runtime_functions(scope):
    """Yield the runtime native functions declared in a scope and its namespaces."""
    for function in scope.native_functions.values():
        if not function.is_constant:
            yield function
    for library in getattr(scope, 'libraries', {}).values():
        yield from runtime_functions(library)


def signature_type(value_type, variables, where):
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
        return signatures.Struct(value_type.name)
    if str(value_type) in ('unit', 'boolean', 'String', 'Duration', 'TimeOfDay', 'MonotonicTime', 'ValueType'):
        return signatures.Builtin(str(value_type))
    raise ValueError(f'{where} uses unsupported type {value_type}')


def signature_port(port, variables, where):
    specification = port_specification(port)
    if specification.resource_type is not None:
        return signatures.Port('resource', signatures.ResourceType(specification.resource_type.native_name),
                               specification.is_list)
    kind_spec = getattr(specification.port_spec, 'kind_spec', None)
    kind = 'event' if kind_spec is not None and kind_spec.kind is ConnectionKind.Event else 'value'
    return signatures.Port(kind, signature_type(specification.var_type, variables, where), specification.is_list)


def procedure_ports(ports, variables, where):
    result = {}
    for port in ports.values():
        port.resolve_reference()
        result[port.declaration.python_key] = signature_port(port, variables, where)
    return result


def type_variables(node_type, uses, where):
    """Map each ValueType parameter in a `uses` clause to the key passing it."""
    variables = {}
    for binding in () if uses is None else uses.bindings:
        expression = binding.expression
        parameter = (node_type.parsed_parameters.get(expression.identifiers[0].id_str)
                     if isinstance(expression, model.Reference) and expression.length == 1 else None)
        if parameter is not None and getattr(parameter, 'type_resolved', None) == types.ValueType:
            if variables.setdefault(parameter, binding.key) != binding.key:
                raise ValueError(f'{where} passes type {expression} under two names')
        elif uses_type(node_type, binding, None, where) == types.ValueType:
            raise ValueError(f'{where} passes a ValueType that is not a parameter: {expression}')
    return variables


def uses_type(node_type, binding, variables, where):
    with binding.expression.context:
        value_type = compile_constant(node_type, binding.expression, None, {}).value_type
    return value_type if variables is None else signature_type(value_type, variables, where)


def signature_uses(node_type, uses, variables, where):
    if uses is None:
        return {}
    return {binding.key: uses_type(node_type, binding, variables, where) for binding in uses.bindings}


def native_signatures(graph, namespace=None):
    """Return the signatures of native state types, procedures, and runtime functions in a graph.

    With a namespace, only declarations whose native names are in it are described.
    Raises ValueError for interfaces that cannot be described.
    """
    def included(name):
        return namespace is None or name.startswith(namespace + '.')

    state_types = {}
    procedures = {}
    for node_type in node_types(graph):
        initializer = node_type.initializer if node_type.is_native else None
        if initializer is not None and included(node_type.native_name):
            where = f'Native state type {node_type.native_name}'
            variables = type_variables(node_type, initializer.uses, where)
            depends = {}
            for port, dependency in zip(initializer.depends.dependencies,
                                        initializer.depends.get_resolved_dependencies()):
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
    functions = {}
    for function in runtime_functions(graph):
        if not included(function.native_name):
            continue
        where = f'Native function {function.native_name}'
        function.resolve_signature()
        functions[function.native_name] = signatures.Function(
            [signature_type(value_type, {}, where) for value_type in function.parameter_types],
            signature_type(function.return_type, {}, where))
    return state_types, procedures, functions
