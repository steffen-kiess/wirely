"""Names of runtime instances and their members."""


def member_name(owner: str, member: str) -> str:
    """Join a member to an ordinary or singleton owner path."""
    if not owner or owner == '<global>':
        return member
    return owner + ('' if owner.endswith(':') else '.') + member


def member_owner(name: str) -> str:
    """Return an instance symbol's owner, preserving singleton roots.

    Generated member labels start with '<' and can contain source filenames
    and other punctuation. Only the prefix before that label is a path.
    """
    prefix, generated, _ = name.partition('<')
    if generated:
        return prefix.removesuffix('.') or '<global>'
    root, singleton, path = name.partition(':')
    if singleton:
        owner, _, _ = path.rpartition('.')
        return root + ':' + owner
    owner, _, _ = name.rpartition('.')
    return owner or '<global>'
