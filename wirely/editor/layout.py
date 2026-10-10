"""Left-to-right layered layout of graph items.

Positions are top-left corners, x to the right and y downwards. The layered
positions do not depend on stored positions, so moving one item leaves the
automatic positions of the others unchanged; only an unplaced item that would
overlap a placed one moves below it.
"""

from dataclasses import dataclass

COLUMN_GAP = 80
ROW_GAP = 30
SWEEPS = 4


@dataclass(frozen=True)
class LayoutItem:
    id: object
    width: float
    height: float
    position: tuple | None = None
    # 'first' or 'last' to force the first or last column.
    column: str | None = None


def layout(items, edges):
    """Return {ID: (x, y)} for items; edges are (source ID, target ID) pairs."""
    items = list(items)
    if not items:
        return {}
    index = {item.id: number for number, item in enumerate(items)}
    successors = [[] for _ in items]
    for source, target in _acyclic(len(items), _edge_indices(index, edges)):
        successors[source].append(target)
    predecessors = [[] for _ in items]
    for source, targets in enumerate(successors):
        for target in targets:
            predecessors[target].append(source)

    columns = _columns(items, successors, predecessors)
    rows = _order(columns, successors, predecessors)
    positions = _coordinates(items, rows)
    _avoid_placed(items, positions)
    return {item.id: item.position or positions[number] for number, item in enumerate(items)}


def _edge_indices(index, edges):
    result = []
    for source, target in edges:
        source = index.get(source)
        target = index.get(target)
        if source is not None and target is not None and source != target \
                and (source, target) not in result:
            result.append((source, target))
    return result


def _acyclic(count, edges):
    """Reverse the back edges found by a depth-first search in item order."""
    adjacent = [[] for _ in range(count)]
    for source, target in edges:
        adjacent[source].append(target)
    state = [0] * count
    back = set()
    for root in range(count):
        if state[root]:
            continue
        state[root] = 1
        stack = [(root, iter(adjacent[root]))]
        while stack:
            node, successors = stack[-1]
            successor = next(successors, None)
            if successor is None:
                state[node] = 2
                stack.pop()
            elif state[successor] == 1:
                back.add((node, successor))
            elif state[successor] == 0:
                state[successor] = 1
                stack.append((successor, iter(adjacent[successor])))
    result = []
    for source, target in edges:
        edge = (target, source) if (source, target) in back else (source, target)
        if edge not in result:
            result.append(edge)
    return result


def _columns(items, successors, predecessors):
    count = len(items)
    column = [0] * count
    for node in _topological(count, successors, predecessors):
        for successor in successors[node]:
            column[successor] = max(column[successor], column[node] + 1)
    # Move sources next to their first target.
    for node in range(count):
        if not predecessors[node] and successors[node] and items[node].column != 'first':
            column[node] = max(0, min(column[successor] for successor in successors[node]) - 1)
    for node, item in enumerate(items):
        if item.column == 'first':
            column[node] = 0
    others = [column[node] for node, item in enumerate(items) if item.column != 'last']
    last = max(others) + 1 if others else 0
    for node, item in enumerate(items):
        if item.column == 'last':
            column[node] = last
    used = {number: rank for rank, number in enumerate(sorted(set(column)))}
    return [used[number] for number in column]


def _topological(count, successors, predecessors):
    remaining = [len(predecessors[node]) for node in range(count)]
    ready = [node for node in range(count) if not remaining[node]]
    order = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for successor in successors[node]:
            remaining[successor] -= 1
            if not remaining[successor]:
                ready.append(successor)
    return order


def _order(column, successors, predecessors):
    """Return the rows of each column, ordered to reduce crossings."""
    rows = [[] for _ in range(max(column) + 1)]
    for node, number in enumerate(column):
        rows[number].append(node)
    rank = {}

    def update():
        for row in rows:
            for position, node in enumerate(row):
                rank[node] = position

    update()
    for _ in range(SWEEPS):
        for sequence, neighbours in ((rows[1:], predecessors), (rows[-2::-1], successors)):
            for row in sequence:
                keys = {}
                for node in row:
                    ranks = [rank[neighbour] for neighbour in neighbours[node]]
                    keys[node] = sum(ranks) / len(ranks) if ranks else rank[node]
                row.sort(key=lambda node: (keys[node], rank[node]))
                update()
    return rows


def _coordinates(items, rows):
    positions = [None] * len(items)
    x = 0
    for row in rows:
        y = 0
        for node in row:
            positions[node] = (x, y)
            y += items[node].height + ROW_GAP
        x += max((items[node].width for node in row), default=0) + COLUMN_GAP
    return positions


def _avoid_placed(items, positions):
    placed = [(item.position, item) for item in items if item.position is not None]
    if not placed:
        return
    occupied = [(x, y, x + item.width, y + item.height) for (x, y), item in placed]
    for number, item in enumerate(items):
        if item.position is not None:
            continue
        x, y = positions[number]
        moved = True
        while moved:
            moved = False
            for left, top, right, bottom in occupied:
                if x < right and left < x + item.width and y < bottom and top < y + item.height:
                    y = bottom + ROW_GAP
                    moved = True
        positions[number] = (x, y)
        occupied.append((x, y, x + item.width, y + item.height))
