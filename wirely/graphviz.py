import graphviz
import wirely as blocks

def htmlescape(s):
    return s.replace('&', '&amp;').replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

def get_graph(graph, engine=None):
    digraph = graphviz.Digraph()

    for block_name in graph.blocks:
        block = graph.blocks[block_name]

        # label = f'{block.name}\n{block.block_type.name}'

        inputs = ''
        for input in block.all_block_inputs:
            label = input.name
            if label == '':
                label = '()'
            if engine is not None and engine.cache is not None:
                key = blocks.path_resolve_singleton((block,)), input.var_identifier
                if key in engine.program.variable_map:
                    label += f' = {engine.get_bound_value(engine.program.variable_map[key])}'
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
        for output in block.block_outputs:
            label = output.name
            if label == '':
                label = '()'
            if engine is not None and engine.cache is not None:
                key = blocks.path_resolve_singleton((block,)), output.var_identifier
                if key in engine.program.variable_map:
                    label += f' = {engine.get_bound_value(engine.program.variable_map[key])}'
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
            <TD>{htmlescape(block.name)}</TD>
          </TR>
          <TR>
            <TD>{htmlescape(block.block_type.name.id_str)}</TD>
          </TR>
        </TABLE>
        </TD></TR>
        {outputs}
        </TABLE>
        >'''
        # digraph.node(block.name, label, shape='box')
        digraph.node(block.name, s, shape='plaintext')

    for block_name in graph.blocks:
        block = graph.blocks[block_name]
        for input in block.all_block_inputs:
            if input.connected_source is not None:
                source = input.connected_source
                source_block = source.block
                if isinstance(source, blocks.BlockStateVariable):
                    digraph.node(source_block.name, label=source.name + '.old')

                # label = f'{source.name} -> {input.name}'
                # digraph.edge(source_block.name, block.name, label=label)

                # edge_name = block.name + '/' + input.name
                # digraph.node(edge_name, '', shape='point')
                # digraph.edge(source_block.name, edge_name, label=source.name)
                # digraph.edge(edge_name, block.name, label=input.name)

                digraph.edge(source_block.name + ':' + source.name, block.name + ':' + input.name)
        digraph.node(block_name)

    return digraph


def run(args):
    import wirely.compiler

    graph = wirely.compiler.compile_files([args.input_filename])
    if graph is None:
        return 1

    get_graph(graph).view()
