"""Native function declarations."""

from wirely.compiler import model

from .base import SourceElement, ProgramError, Declaration


class FunctionArgument(SourceElement):
    def __init__(self, name, ty, *, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.ty = ty


class DeclarationNativeFunction(Declaration):
    def __init__(self, name, arguments, return_type, *, is_constant=False, source_position):
        super().__init__(source_position=source_position)
        self.name = name
        self.arguments = arguments
        self.return_type_ast = return_type
        # Constant functions are compile-time only; others are also callable in procedure bodies.
        self.is_constant = is_constant

    @staticmethod
    def create_constant(name, arguments, return_type, *, source_position):
        return DeclarationNativeFunction(name, arguments, return_type, is_constant=True,
                                         source_position=source_position)

    def compile_pass1(self, graph):
        from ..native_functions import validate_entry
        if not isinstance(graph, model.ast.DeclarationLibrary) and graph.name is not None:
            raise ProgramError(self, 'Native functions are only allowed at root or in namespaces')
        self.scope = graph
        self.function_name = getattr(graph, 'fullname_tuple', ()) + (self.name.id_str,)
        if self.function_name == ('Some',):
            raise ProgramError(self.name, 'Some is reserved for the Option constructor')
        names = [argument.name.id_str for argument in self.arguments]
        if len(set(names)) != len(names):
            raise ProgramError(self, 'Duplicate function argument name')
        self.argument_names = tuple(names)
        if self.is_constant:
            validate_entry(self.function_name, self)
        graph.native_functions[self.name.id_str] = self

    def resolve_signature(self):
        if not hasattr(self, 'parameter_types'):
            self.parameter_types = [self.scope.resolve_type(argument.ty) for argument in self.arguments]
            self.return_type = self.scope.resolve_type(self.return_type_ast)
            if any(ty.contains_parameters for ty in self.parameter_types + [self.return_type]):
                raise ProgramError(self, 'Native functions require fixed value types')
            if not self.is_constant:
                for ty in self.parameter_types + [self.return_type]:
                    if not ty.is_runtime_type:
                        name = '.'.join(self.function_name)
                        raise ProgramError(self, f'Function {name!r} uses parameter-only type {ty}; '
                                           'declare it native constant function')
        return self

    def invoke(self, values, sources, call):
        if self.is_constant:
            from ..native_functions import invoke
            return invoke(self, values, sources, call)
        from ..native_functions import invoke_runtime_function
        return invoke_runtime_function(self, values, call)

    def compile_pass1_5(self, graph):
        self.resolve_signature()

    def compile_pass1_btd(self, interface):
        pass

    def create_io(self, graph):
        pass

    def compile_pass2(self, graph):
        pass
