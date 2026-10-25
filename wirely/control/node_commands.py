"""Typed client commands of node types, such as `set`, mapped to their native commands.

Values are given and printed in the value text format and travel in the runtime binary
representation, as the libraries document for their commands.
"""

from dataclasses import dataclass
from typing import Any

from ..program import Program
from ..program.program import procedure_instances
from ..runtime.extensions import NativeTypes
from ..types import Type, TypeOption


@dataclass(frozen=True)
class TypedCommand:
    number: int  # Native command number.
    takes_value: bool


@dataclass(frozen=True)
class NodeCommands:
    commands: dict[str, TypedCommand]
    event: bool  # Whether the default output is an event, whose payload the commands use.


# Typed commands by the native name of a node type's initializer, as in doc/libraries.md.
NODE_COMMANDS = {
    'Std.DebugValueSource:create': NodeCommands(
        {'get': TypedCommand(0, False), 'set': TypedCommand(1, True)}, event=False),
    'Std.DebugEventSource:create': NodeCommands(
        {'get': TypedCommand(0, False), 'send': TypedCommand(1, True)}, event=True),
}


class NodeCommandError(Exception):
    """A typed command that cannot be sent, with a message for the user."""


@dataclass(frozen=True)
class Target:
    """A node with typed commands in a compiled program."""

    name: str
    instance: int  # Index of the node's initializer in ProcedureInstances.
    node_type: str  # Qualified name of the node type.
    commands: dict[str, TypedCommand]
    value_type: Type

    def command(self, name: str) -> TypedCommand:
        try:
            return self.commands[name]
        except KeyError:
            raise NodeCommandError(
                f'`{self.name}` (`{self.node_type}`) has no command `{name}`; '
                f'it has {", ".join(f"`{command}`" for command in self.commands)}') from None

    def encode(self, text: str, native_types: NativeTypes) -> dict[int, Any]:
        """Return the argument of a command that takes the value given as text."""
        try:
            value = self.value_type.parse(text, native_types=native_types)
        except ValueError as error:
            raise NodeCommandError(f'Invalid `{self.value_type}` value {text!r}: {error}') from None
        return {1: self.value_type.serialize(value)}

    def decode(self, result: Any, native_types: NativeTypes) -> str | None:
        """Return the value in a command's result as text, or None if it has none."""
        if not isinstance(result, dict) or 1 not in result:
            return None
        return self.value_type.to_text(self.value_type.deserialize(result[1]), native_types=native_types)


def resolve(program: Program, name: str) -> Target:
    """Find the node with this declared name or path and its typed commands."""
    path = program.symbols.node_aliases.get(name, name)
    procedures = procedure_instances(program)
    for index, procedure in enumerate(procedures):
        if procedure.node == path and procedure.native is not None and procedure.initializer:
            node_commands = NODE_COMMANDS.get(procedure.native.name)
            if node_commands is not None:
                break
    else:
        if any(procedure.node == path for procedure in procedures):
            raise NodeCommandError(f'`{name}` has no typed commands')
        raise NodeCommandError(f'Cannot find node `{name}`')
    output = program.symbols.variable_aliases.get(name) or program.symbols.variable_aliases.get(path)
    if output is None or output.variable is None:
        raise NodeCommandError(f'Cannot find the output of `{name}`')
    value_type = output.variable.var_type
    if node_commands.event:
        assert isinstance(value_type, TypeOption)
        value_type = value_type.inner_type
    assert procedure.native is not None
    return Target(name, index, procedure.native.name.partition(':')[0], node_commands.commands, value_type)


def command_targets(program: Program) -> dict[str, set[str]]:
    """Return the declared node names that support each typed command, for completion."""
    targets: dict[str, set[str]] = {}
    for name in program.symbols.node_aliases:
        try:
            target = resolve(program, name)
        except NodeCommandError:
            continue
        for command in target.commands:
            targets.setdefault(command, set()).add(name)
    return targets


@dataclass(frozen=True)
class TreeNode:
    """A node in the node tree of a program."""

    depth: int
    name: str  # The last component of the path.
    path: str  # The declared node path.
    node_type: str
    target: Target | None  # The node's typed commands, if any.


def node_tree(program: Program, root: str | None = None) -> list[TreeNode]:
    """Return the nodes of a program, or of the subtree at root, in declaration order."""
    if program.graph is None:
        raise NodeCommandError('Listing nodes needs the program compiled from its sources')
    nodes: list[TreeNode] = []

    def visit(node_type: Any, prefix: str, depth: int) -> None:
        for name, child in node_type.node_declarations.items():
            path = prefix + name
            try:
                target: Target | None = resolve(program, path)
            except NodeCommandError:
                target = None
            child_type = child.node_type
            nodes.append(TreeNode(depth, name, path, getattr(child_type, 'qualified_name', None)
                                  or child_type.fullname, target))
            visit(child_type, path + '.', depth + 1)

    visit(program.graph, '', 0)
    if root is None:
        return nodes
    subtree = [node for node in nodes if node.path == root or node.path.startswith(root + '.')]
    if not subtree:
        raise NodeCommandError(f'Cannot find node `{root}`')
    base = subtree[0].depth
    return [TreeNode(node.depth - base, node.name, node.path, node.node_type, node.target) for node in subtree]
