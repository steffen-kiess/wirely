import graphviz
import wirely
from wirely.compiler import model

def htmlescape(s):
    return s.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

def get_graph(graph, engine=None):
    digraph = graphviz.Digraph()

    for node_name in graph.instance_nodes:
        node = graph.instance_nodes[node_name]

        # label = f'{node.name}\n{node.node_type.name}'

        inputs = ''
        for input in node.all_input_ports:
            label = input.name
            if label == '':
                label = '()'
            # Default inputs have no alias; their edge shows the source.
            if engine is not None and engine.cache is not None and input.name != '':
                name = node.name + '.' + input.name
                if name in engine.program.variables:
                    label += f' = {engine.get_value(name)}'
                else:
                    # TODO: Handle this
                    label += f' = MISSING'
            inputs += f'<TD PORT="{htmlescape(input.name)}">{htmlescape(label)}</TD>'
        if inputs != '':
            inputs = f'''
            <TR><TD>
            <TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
              <TR>{inputs}</TR>
            </TABLE>
            </TD></TR>
            '''

        outputs = ''
        for output in node.output_ports:
            label = output.name
            if label == '':
                label = '()'
            if engine is not None and engine.cache is not None:
                name = node.name + '.' + output.name if output.name else node.name
                if name in engine.program.variables:
                    label += f' = {engine.get_value(name)}'
                else:
                    # TODO: Handle this
                    label += f' = MISSING'
            outputs += f'<TD PORT="{htmlescape(output.name)}">{htmlescape(label)}</TD>'
        if outputs != '':
            outputs = f'''
            <TR><TD>
            <TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
              <TR>{outputs}</TR>
            </TABLE>
            </TD></TR>
            '''

        s = f'''<
        <TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0">
        {inputs}
        <TR><TD>
        <TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0">
          <TR>
            <TD>{htmlescape(node.name)}</TD>
          </TR>
          <TR>
            <TD>{htmlescape(node.node_type.name.id_str)}</TD>
          </TR>
        </TABLE>
        </TD></TR>
        {outputs}
        </TABLE>
        >'''
        # digraph.node(node.name, label, shape='box')
        digraph.node(node.name, s, shape='plaintext')

    for node_name in graph.instance_nodes:
        node = graph.instance_nodes[node_name]
        for input in node.all_input_ports:
            if input.connected_source is not None:
                source = input.connected_source
                source_node = source.node
                if isinstance(source, model.StateRead):
                    source_name = source.name + '.' + source.phase
                    digraph.node(source_name, label=source_name)
                    digraph.edge(source_name, node.name + ':' + input.name)
                    continue
                if isinstance(source, model.StateNode):
                    digraph.node(source_node.name, label=source.name + '.old')
                if isinstance(source, model.TemporaryNode):
                    digraph.node(source.name, label=source.name)
                    digraph.edge(source.name, node.name + ':' + input.name)
                    continue

                # label = f'{source.name} -> {input.name}'
                # digraph.edge(source_node.name, node.name, label=label)

                # edge_name = node.name + '/' + input.name
                # digraph.node(edge_name, '', shape='point')
                # digraph.edge(source_node.name, edge_name, label=source.name)
                # digraph.edge(edge_name, node.name, label=input.name)

                digraph.edge(source_node.name + ':' + source.name, node.name + ':' + input.name)
        digraph.node(node_name)

    return digraph


def run(args):
    import wirely.compiler

    graph = wirely.compiler.compile_files([args.input_filename])
    if graph is None:
        return 1

    get_graph(graph).view()
