"""Execution of checked, loop-free .wrl procedure bodies."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Expression:
    operation: str
    arguments: tuple = ()
    payload: object = None

    def evaluate(self, inputs, locals):
        operation = self.operation
        if operation == 'constant':
            return self.payload
        if operation == 'input':
            return inputs[self.payload]
        if operation == 'local':
            return locals[self.payload]
        if operation == 'block':
            locals = dict(locals)
            for slot, expression in zip(self.payload, self.arguments[:-1]):
                locals[slot] = expression.evaluate(inputs, locals)
            return self.arguments[-1].evaluate(inputs, locals)
        left = self.arguments[0].evaluate(inputs, locals)
        if operation == 'not':
            return not left
        if operation == 'and':
            return left and self.arguments[1].evaluate(inputs, locals)
        if operation == 'or':
            return left or self.arguments[1].evaluate(inputs, locals)
        if operation == 'if':
            branch = self.arguments[1] if left else self.arguments[2]
            return branch.evaluate(inputs, locals)
        right = self.arguments[1].evaluate(inputs, locals)
        if operation == 'eq':
            return left == right
        if operation == 'ne':
            return left != right
        raise AssertionError(f'Unknown checked operation {operation!r}')


@dataclass(frozen=True, slots=True)
class ProcedureBody:
    # (local slot or result name, expression); result names are strings.
    statements: tuple

    def execute(self, inputs):
        locals = {}
        results = {}
        for destination, expression in self.statements:
            value = expression.evaluate(inputs, locals)
            if isinstance(destination, str):
                results[destination] = value
            else:
                locals[destination] = value
        return results
