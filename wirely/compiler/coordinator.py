"""Schedule preparation of reusable node graphs through explicit boundaries."""

import contextlib

from . import model
from .diagnostics import error_diagnostics
from .stages import ResolutionState


class CompilerCoordinator:
    def __init__(self, global_node_type, report_progress=None, *, shuffle_seed=None, diagnostics=None,
                 warnings=None):
        self.global_node_type = global_node_type
        self.warnings = warnings
        self.report_progress = report_progress
        self.shuffle_seed = shuffle_seed
        # A list collects errors and continues after them; None stops at the first error.
        self.diagnostics = diagnostics
        self.node_types = []
        self.declaration_groups = []
        self.containment = {}
        self.population_order = []
        global_node_type.coordinator = self

    def source_order(self, element):
        position = element.source_position
        order = (str(position.filename or ''), getattr(position.meta, 'start_pos', -1))
        if self.shuffle_seed is not None:
            from .shuffle import shuffled_source_order
            return shuffled_source_order(order, self.shuffle_seed)
        return order

    @contextlib.contextmanager
    def contain(self, element):
        """Record errors inside as diagnostics and mark element as failed, unless stopping at errors."""
        if self.diagnostics is None:
            yield
            return
        try:
            yield
        except model.ProgramError as error:
            self._report(error)
        except Exception as error:
            self._report(model.InternalCompilerError(element, error))
        else:
            return
        if element is not None:
            element.failed = True

    def _report(self, error):
        # An error can be found again through elements depending on a failed one.
        for diagnostic in error_diagnostics(error):
            if diagnostic not in self.diagnostics:
                self.diagnostics.append(diagnostic)

    def compile(self):
        self._run((
            self.collect_declarations, self.prepare_interfaces,
            self.resolve_instance_declarations, self.check_containment_cycles,
            self.create_child_interfaces, self.bind_bodies, self.validate_bodies,
            self.populate_instances))
        if self.report_progress is not None:
            self.report_progress('Finished graph compilation')

    def compile_interfaces(self):
        """Run the stages up to the creation of child instances and their ports."""
        self._run((
            self.collect_declarations, self.prepare_interfaces,
            self.resolve_instance_declarations, self.check_containment_cycles,
            self.create_child_interfaces))

    def _run(self, operations):
        for operation in operations:
            if self.report_progress is not None:
                self.report_progress(f'Starting {operation.__name__.replace("_", " ")}')
            operation()

    def collect_declarations(self):
        self.global_node_type.collect_declarations(None)

    def prepare_interfaces(self):
        for node_type in self.node_types:
            node_type.prepare_interface()
        for scope, declarations in self.declaration_groups:
            if not isinstance(scope, model.DeclarationLibrary):
                continue
            for declaration in declarations:
                if not isinstance(declaration, (model.NodeType, model.DeclarationLibrary)) and not _failed(declaration):
                    with self.contain(declaration), declaration.context:
                        declaration.prepare_interface(scope)

    def resolve_instance_declarations(self):
        for node_type in self.node_types:
            node_type.resolve_instance_declarations()
            self.containment[node_type] = sorted(
                (declaration for declaration in node_type.node_declarations.values() if not _failed(declaration)),
                key=self.source_order)

    def check_containment_cycles(self):
        """Only child instantiation creates a dependency between type bodies."""
        complete = set()
        active = []
        edges: list = []

        def visit(node_type):
            if node_type in complete:
                return
            active.append(node_type)
            for declaration in self.containment[node_type]:
                if _failed(declaration):
                    continue
                child = declaration.node_type
                if child in active:
                    cycle = edges[active.index(child):]
                    with self.contain(declaration):
                        raise model.ProgramError(
                            declaration, f'Recursive node type instantiation of {child.fullname!r}',
                            hints=[model.CompilerHint(edge, 'Instantiation continues here') for edge in cycle],
                        )
                    continue
                edges.append(declaration)
                visit(child)
                edges.pop()
            active.pop()
            complete.add(node_type)
            self.population_order.append(node_type)

        for node_type in sorted(self.node_types, key=self.source_order):
            visit(node_type)

    def create_child_interfaces(self):
        for node_type in self.node_types:
            for declaration in node_type.node_declarations.values():
                if _failed(declaration):
                    continue
                with self.contain(declaration), declaration.context:
                    declaration.create_instance_interface(node_type)

    def resolve_procedure_ports(self):
        """Give reference procedure ports the specification of the port they reference."""
        for node_type in self.node_types:
            ports = [port for procedure in node_type.procedures.values() if not _failed(procedure)
                     for port in (*procedure.input_ports.values(), *procedure.output_ports.values())]
            for port in sorted(ports, key=lambda port: self.source_order(port.declaration)):
                if port.reference_state is ResolutionState.FAILED:
                    continue
                with self.contain(port.declaration), port.declaration.context:
                    port.resolve_reference()

    def bind_bodies(self):
        self.resolve_procedure_ports()
        for node_type in self.node_types:
            node_type.bind_body()

    def validate_bodies(self):
        for node_type in self.node_types:
            node_type.validate_body()

    def populate_instances(self):
        for node_type in self.population_order:
            for node in node_type.instance_nodes.values():
                with node.instantiation.context:
                    node.node_type.populate_instance(node)


def _failed(element):
    return getattr(element, 'failed', False)
