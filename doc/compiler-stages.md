# Compiler stages

The compiler first builds graphs describing node types and their connections.
It then uses those graphs to generate a runtime program. Use the Python API
in two steps:

- `wirely.compiler.compile_files()` reads `.wrl` files; `compile_strs()`
  accepts filename/text pairs. Both return the graph of top-level
  declarations, called the global graph, after resolving declarations,
  connecting ports, and checking the graphs.
- `wirely.compiler.program.compile()` takes that graph, evaluates each
  instance's parameters, and generates a runtime program.

Both parts report language errors through `on_diagnostic`, or print them
when no callback is supplied, and return `None` on failure. For example,
`compile_files()` reports an unknown node type or a connection with the
wrong value type. `program.compile()` reports a failed assertion that depends
on an instance's parameters or a cycle in procedure execution dependencies.

## Read and parse sources

1. **Load sources.** `compile_files()` reads the requested files.
   `compile_strs()` accepts their contents directly. Both include standard
   library sources when `add_std` is enabled.
2. **Parse sources.** `parser.parse_sources()` splits each file into tokens,
   checks its grammar, and builds a syntax tree of declarations and
   expressions. The tree retains attributes and source positions so later
   stages can report errors at the relevant text.
3. **Validate source metadata.** `validate_source_metadata()` checks file
   attributes and native function name collisions.

The source outline and editing helpers use
`parser.parse_str()` directly. They stop at parsing and syntax-tree
construction, so they can read and edit files with errors that would be
detected by later compiler stages.

## Build node type graphs

`CompilerCoordinator` runs the following stages for all declared node types,
including unused ones. Top-level declarations go through the same stages.
A type's graph describes its ports, storage, procedures, and child nodes;
parameter values can still depend on how the type will be instantiated.

1. **`collect_declarations`.** Register names and check duplicates and
   restrictions on where declarations may appear. Combine repeated namespace
   blocks into the same scope. Collect nested node types for processing
   independently of the types that enclose them.
2. **`prepare_interfaces`.** Resolve parameter types, struct fields, and
   function signatures. Create objects for declared node ports, procedure
   ports, and local storage. A procedure port declared by reference gets its
   type from the referenced port during `bind_bodies`. This stage prepares
   each type's own declarations; the node instances inside its body are
   resolved next.
3. **`resolve_instance_declarations`.** Find the type of each child node and
   check its parameter assignments. Keep references to enclosing parameters
   for later evaluation. Record which types instantiate other types.
4. **`check_containment_cycles`.** Reject a type that directly or indirectly
   instantiates itself, including in unused types. Report the declarations
   forming the cycle. Declaring a type inside another type does not itself
   instantiate it.
5. **`create_child_interfaces`.** Create child instance objects and their
   input and output ports with `NodeType.create_instance_interface()`.
   All child ports exist before `bind_bodies` resolves references and makes
   connections. Storage and procedures inside those instances are filled in
   later.
6. **`bind_bodies`.** Resolve procedure port references and the types they
   inherit. Check parameter defaults, constants, and assertion expressions.
   Resolve initializer dependencies and connect ports named in procedure
   dependency and output declarations. Then apply explicit `connect`
   statements in their relative source order. This order preserves
   multi-input ordering and the choice of storage for procedure outputs.
7. **`validate_bodies`.** Check that procedure inputs and local storage have
   sources. Reject local aliases that refer back to themselves. Each body,
   including the top-level body, is now connected and checked.
8. **`populate_instances`.** Fill in existing child instances with storage,
   procedures, aliases, state-copy sources, and initializer dependencies
   from their type graphs, using `NodeType.populate_instance()`. Process
   child types before the types that instantiate them. Keep the same port
   objects so previously established connections remain valid.

The coordinator controls this order. Creating or populating an instance
never starts another compiler stage. Each preparation operation is marked
complete only after it succeeds. Types and constants that depend on other
declarations can resolve those dependencies when first needed, with separate
checks for cycles in those references.

## Generate the runtime program

`program.compile()` calls `program.lower_program()` with the global graph.
If there are top-level storage declarations, procedures, constants, or
assertions, it creates an instance representing those top-level declarations.

`program.specialize_instances()` visits each use of a node type. Through
`InstanceNode.specialize()`, it evaluates parameters, defaults, constants,
and assertions, resolves parameter-dependent types, and allocates state
storage. It also assigns instance paths and shares singleton instances.
For example, two nodes using the same type can have different parameter
values and separate storage even though they use the same type graph.

The remaining work in `lower_program()` maps connection sources to runtime
storage, orders procedures and native node construction after their
dependencies, translates Wirely procedure bodies into instructions, and
builds the runtime program tables. Checks that require an instance's actual
parameter values or procedure port types happen during this part of
`program.compile()`.
