"""Schedule preparation of reusable node graphs through explicit boundaries."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Iterable, Iterator

from . import model
from .diagnostics import Diagnostic, WarningSink, error_diagnostics
from .stages import ResolutionState


class CompilerCoordinator:
    def __init__(self, global_node_type: model.NodeType, report_progress: Callable[[str], None] | None = None, *,
                 shuffle_seed: int | None = None, diagnostics: list[Diagnostic] | None = None,
                 warnings: WarningSink | None = None, timings: list[tuple[str, float]] | None = None) -> None:
        self.global_node_type = global_node_type
        self.warnings = warnings
        self.report_progress = report_progress
        self.shuffle_seed = shuffle_seed
        # Receives the name and duration of each completed stage, if given.
        self.timings = timings
        # A list collects errors and continues after them; None stops at the first error.
        self.diagnostics = diagnostics
        self.node_types: list[model.NodeType] = []
        self.declaration_groups: list[tuple[model.Scope, list[model.Declaration]]] = []
        self.containment: dict[model.NodeType, list[model.DeclarationInstance]] = {}
        self.population_order: list[model.NodeType] = []
        global_node_type.coordinator = self

    def source_order(self, element: model.ast.SourceElement) -> tuple[str, int] | tuple[bytes, str, int]:
        position = element.source_position
        order = (str(position.filename or ''), getattr(position.meta, 'start_pos', -1))
        if self.shuffle_seed is not None:
            from .shuffle import shuffled_source_order
            return shuffled_source_order(order, self.shuffle_seed)
        return order

    @contextlib.contextmanager
    def contain(self, element: model.ast.SourceElement | None, *,
                failures: set | None = None) -> Iterator[None]:
        """Record errors inside as diagnostics and mark element as failed, unless stopping at errors.

        With failures, add a failed element to it instead of marking it. Syntax elements are
        immutable and only recorded in failures.
        """
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
        if failures is not None:
            failures.add(element)
        elif element is not None and not isinstance(element, model.ast.SyntaxElement):
            element.failed = True

    def _report(self, error: model.ProgramError) -> None:
        assert self.diagnostics is not None
        # An error can be found again through elements depending on a failed one.
        for diagnostic in error_diagnostics(error):
            if diagnostic not in self.diagnostics:
                self.diagnostics.append(diagnostic)

    def compile(self) -> None:
        self._run((
            self.collect_declarations, self.prepare_interfaces,
            self.resolve_instance_declarations, self.check_containment_cycles,
            self.create_child_interfaces, self.bind_bodies, self.validate_bodies,
            self.populate_instances))
        if self.report_progress is not None:
            self.report_progress('Finished graph compilation')

    def compile_interfaces(self) -> None:
        """Run the stages up to the creation of child instances and their ports."""
        self._run((
            self.collect_declarations, self.prepare_interfaces,
            self.resolve_instance_declarations, self.check_containment_cycles,
            self.create_child_interfaces))

    def _run(self, operations: Iterable[Callable[[], None]]) -> None:
        for operation in operations:
            name = operation.__name__.replace('_', ' ')
            if self.report_progress is not None:
                self.report_progress(f'Starting {name}')
            start = time.perf_counter()
            operation()
            if self.timings is not None:
                self.timings.append((name, time.perf_counter() - start))

    def collect_declarations(self) -> None:
        self.global_node_type.collect_declarations()

    def prepare_interfaces(self) -> None:
        for node_type in self.node_types:
            node_type.prepare_type_interface()
        for scope, declarations in self.declaration_groups:
            if not isinstance(scope, model.Namespace):
                continue
            for declaration in declarations:
                if not isinstance(declaration, (model.DeclarationNodeType, model.DeclarationLibrary)) and not _failed(declaration):
                    with self.contain(declaration), declaration.context:
                        declaration.prepare_interface(scope)

    def resolve_instance_declarations(self) -> None:
        for node_type in self.node_types:
            node_type.resolve_instance_declarations()
            self.containment[node_type] = sorted(
                (declaration for declaration in node_type.node_declarations.values() if not _failed(declaration)),
                key=self.source_order)

    def check_containment_cycles(self) -> None:
        """Only child instantiation creates a dependency between type bodies."""
        complete: set[model.NodeType] = set()
        active: list[model.NodeType] = []
        edges: list[model.DeclarationInstance] = []

        def visit(node_type: model.NodeType) -> None:
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

        for node_type in sorted(self.node_types, key=lambda node_type: self.source_order(node_type.declaration)):
            visit(node_type)

    def create_child_interfaces(self) -> None:
        for node_type in self.node_types:
            for declaration in node_type.node_declarations.values():
                if _failed(declaration):
                    continue
                with self.contain(declaration), declaration.context:
                    declaration.create_instance_interface(node_type)

    def resolve_procedure_ports(self) -> None:
        """Give reference procedure ports the specification of the port they reference."""
        for node_type in self.node_types:
            ports = [port for procedure in node_type.procedures.values() if not _failed(procedure)
                     for port in (*procedure.input_ports.values(), *procedure.output_ports.values())]
            for port in sorted(ports, key=lambda port: self.source_order(port.declaration)):
                if port.reference_state is ResolutionState.FAILED:
                    continue
                with self.contain(port.declaration), port.declaration.context:
                    port.resolve_reference()

    def bind_bodies(self) -> None:
        self.resolve_procedure_ports()
        for node_type in self.node_types:
            node_type.bind_type_body()

    def validate_bodies(self) -> None:
        for node_type in self.node_types:
            node_type.validate_body()

    def populate_instances(self) -> None:
        for node_type in self.population_order:
            for node in node_type.instance_nodes.values():
                with node.instantiation.context:
                    node.node_type.populate_instance(node)


def _failed(element: model.ast.SourceElement) -> bool:
    return getattr(element, 'failed', False)
