from unittest import TestCase

from wirely.compiler import parser
from wirely.compiler.attribute_editing import (
    Connection, Node, NodeType, add_attribute, format_metadata, remove_attribute, set_attribute,
)


checks = TestCase()
position = format_metadata('position', x=1, y=-2.5)
checks.assertEqual(position, 'position(x = 1, y = -2.5)')
checks.assertEqual(format_metadata('internal'), 'internal')
checks.assertEqual(format_metadata('flags', on=True, off=False, size=1e20),
                   'flags(on = true, off = false, size = 1e+20)')
with checks.assertRaises(ValueError):
    format_metadata('position', x=float('nan'), y=0)


def check(function, source, target, argument, expected):
    result = function(source, target, argument)
    checks.assertEqual(result, expected)
    parser.parse_str(result, filename='<result>')


# Multi-line bodies keep comments and follow the body's indentation.
check(add_attribute, '''node a: Std.Constant { // comment
    Type: boolean, // type
    Value: true,
}
''', Node(('a',)), position, '''node a: Std.Constant { // comment
    #![position(x = 1, y = -2.5)]
    Type: boolean, // type
    Value: true,
}
''')
check(add_attribute, '''node a: Std.Constant {
  #![internal] // after
  Type: boolean,
}
''', Node(('a',)), position, '''node a: Std.Constant {
  #![internal] // after
  #![position(x = 1, y = -2.5)]
  Type: boolean,
}
''')
check(add_attribute, '''node a: Std.Constant { Type: boolean,
  Value: true,
}
''', Node(('a',)), 'internal', '''node a: Std.Constant { #![internal] Type: boolean,
  Value: true,
}
''')
check(add_attribute, 'node a: T {\n}\n', Node(('a',)), 'internal',
      'node a: T {\n  #![internal]\n}\n')

# One-line bodies are expanded using the file's indentation unit.
check(add_attribute, '''namespace A.B {
    node type T {
        node a: Std.Constant { Type: boolean, Value: true }
        node b: U /* type */ {}
    }
}
''', Node(('A', 'B', 'T', 'a')), position, '''namespace A.B {
    node type T {
        node a: Std.Constant {
            #![position(x = 1, y = -2.5)]
            Type: boolean, Value: true
        }
        node b: U /* type */ {}
    }
}
''')
check(add_attribute, 'node type T {\n\tnode b: U /* type */ {}\n}\n', Node(('T', 'b')), position,
      'node type T {\n\tnode b: U /* type */ {\n\t\t#![position(x = 1, y = -2.5)]\n\t}\n}\n')
check(add_attribute, 'node a: T { #![internal] P: 1 }\n', Node(('a',)), position,
      'node a: T {\n  #![internal]\n  #![position(x = 1, y = -2.5)]\n  P: 1\n}\n')

# Setting replaces attributes in place and removes duplicates.
check(set_attribute, '''#[position(x = 0, y = 0)] // outer
node a: T {
  #![position(x = 5, y = 5)] // inner
}
''', Node(('a',)), position, '''#[position(x = 1, y = -2.5)] // outer
node a: T {
  // inner
}
''')
check(set_attribute, 'node a: T { #![position(x = 0, y = 0)] }\n', Node(('a',)), position,
      'node a: T { #![position(x = 1, y = -2.5)] }\n')
check(set_attribute, 'node a: T {}\n', Node(('a',)), position,
      'node a: T {\n  #![position(x = 1, y = -2.5)]\n}\n')

# Removal drops lines that only held the attribute.
check(remove_attribute, '''node a: T {
  #![internal]
  P: 1,
}
''', Node(('a',)), 'internal', 'node a: T {\n  P: 1,\n}\n')
check(remove_attribute, 'node a: T { #![internal] }\n', Node(('a',)), 'internal',
      'node a: T { }\n')
check(remove_attribute, 'connect b -> c; #[internal]\nnode a: T {}\n', Node(('a',)), 'internal',
      'connect b -> c;\nnode a: T {}\n')
check(remove_attribute, 'node a: T {}\n', Node(('a',)), 'internal', 'node a: T {}\n')

# Node types and repeated namespaces.
check(add_attribute, '''namespace N {
  node a: T {}
}
namespace N {
  node type T {
    node b: U {}
  }
}
''', NodeType(('N', 'T')), 'internal', '''namespace N {
  node a: T {}
}
namespace N {
  node type T {
    #![internal]
    node b: U {}
  }
}
''')

# Connections use outer attributes for single sources and per-source ones otherwise.
check(add_attribute, 'node type T {\n  connect a.out -> b;\n}\n',
      Connection(('T',), 'a.out', 'b'), 'internal',
      'node type T {\n  #[internal]\n  connect a.out -> b;\n}\n')
check(set_attribute, 'connect #[x = 1] a, b -> default output;\n',
      Connection((), 'a', 'default output'), 'x = 2',
      'connect #[x = 2] a, b -> default output;\n')
check(add_attribute, 'connect a, b -> c;\n', Connection((), 'b', 'c'), 'x = 2',
      'connect a, #[x = 2] b -> c;\n')
check(remove_attribute, '#[x]\nconnect #[x] a -> c;\n', Connection((), 'a', 'c'), 'x',
      'connect a -> c;\n')
with checks.assertRaises(ValueError):
    set_attribute('#[x]\nconnect a, b -> c;\n', Connection((), 'a', 'c'), 'x')
check(add_attribute, 'connect a, a -> c;\n', Connection((), 'a', 'c', 1), 'x',
      'connect a, #[x] a -> c;\n')
with checks.assertRaises(ValueError):
    add_attribute('connect a, a -> c;\n', Connection((), 'a', 'c'), 'x')

# Files with semantic errors are edited, but targets and metadata are checked.
check(add_attribute, 'node a: Missing.Type { Unknown: 1 + true }\nconnect a -> nowhere;\n',
      Node(('a',)), position,
      'node a: Missing.Type {\n  #![position(x = 1, y = -2.5)]\n  Unknown: 1 + true\n}\n'
      'connect a -> nowhere;\n')
for target in (Node(('b',)), NodeType(('a',)), Connection((), 'a', 'b')):
    with checks.assertRaises(ValueError):
        add_attribute('node a: T {}\n', target, 'internal')
with checks.assertRaises(ValueError):
    add_attribute('node a: T {}\nnode a: T {}\n', Node(('a',)), 'internal')
for metadata in ('', 'x = y', 'x] node b: T {} #![y', 'x // ]'):
    with checks.assertRaises(ValueError):
        add_attribute('node a: T {}\n', Node(('a',)), metadata)
