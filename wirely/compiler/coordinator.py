"""Schedule preparation of reusable node graphs through explicit boundaries."""

from . import model


def source_order(element):
    position = element.source_position
    meta = position.meta
    return (position.filename or '', getattr(meta, 'start_pos', -1))


class CompilerCoordinator:
    def __init__(self, global_node_type, report_progress=None):
        self.global_node_type = global_node_type
        self.report_progress = report_progress
        self.node_types = []
        self.declaration_groups = []
        self.containment = {}
        self.population_order = []
        global_node_type.coordinator = self

    def compile(self):
        for operation in (
                self.collect_declarations, self.prepare_interfaces,
                self.resolve_instance_declarations, self.check_containment_cycles,
                self.create_child_interfaces, self.bind_bodies, self.validate_bodies,
                self.populate_instances):
            if self.report_progress is not None:
                self.report_progress(f'Starting {operation.__name__.replace("_", " ")}')
            operation()
        if self.report_progress is not None:
            self.report_progress('Finished graph compilation')

    def collect_declarations(self):
        self.global_node_type.compile_pass1(None)

    def prepare_interfaces(self):
        for node_type in self.node_types:
            node_type.prepare_interface()
        for scope, declarations in self.declaration_groups:
            for declaration in declarations:
                if isinstance(declaration, (model.DeclarationStructType, model.DeclarationNativeFunction)):
                    with declaration.context:
                        declaration.compile_pass1_5(scope)

    def resolve_instance_declarations(self):
        for node_type in self.node_types:
            node_type.resolve_instance_declarations()
            self.containment[node_type] = sorted(node_type.node_declarations.values(), key=source_order)

    def check_containment_cycles(self):
        """Only child instantiation creates a dependency between type bodies."""
        complete = set()
        active = []
        edges = []

        def visit(node_type):
            if node_type in complete:
                return
            active.append(node_type)
            for declaration in self.containment[node_type]:
                child = declaration.node_type
                if child in active:
                    cycle = edges[active.index(child):]
                    raise model.ProgramError(
                        declaration, f'Recursive node type instantiation of {child.fullname!r}',
                        hints=[model.CompilerHint(edge, 'Instantiation continues here') for edge in cycle],
                    )
                edges.append(declaration)
                visit(child)
                edges.pop()
            active.pop()
            complete.add(node_type)
            self.population_order.append(node_type)

        for node_type in sorted(self.node_types, key=source_order):
            visit(node_type)

    def create_child_interfaces(self):
        for node_type in self.node_types:
            for declaration in node_type.node_declarations.values():
                with declaration.context:
                    declaration.compile_pass2(node_type)

    def bind_bodies(self):
        for node_type in self.node_types:
            for procedure in node_type.procedures.values():
                for port in (*procedure.input_ports.values(), *procedure.output_ports.values()):
                    with port.declaration.context:
                        port.resolve_reference()
        for node_type in self.node_types:
            node_type.compile_pass2(node_type.parent_graph)

    def validate_bodies(self):
        # Root validation is still performed by program compilation.
        for node_type in self.node_types:
            if node_type.parent_graph is not None:
                model.ast.validate_nodes(node_type)

    def populate_instances(self):
        for node_type in self.population_order:
            for node in node_type.instance_nodes.values():
                with node.instantiation.context:
                    node.node_type.populate_instance(node)
