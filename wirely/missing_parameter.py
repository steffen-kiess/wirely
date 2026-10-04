"""Marker for a declared native binding of an omitted optional parameter."""


class _MissingParameter:
    __slots__ = ()

    def __repr__(self):
        return 'MISSING_PARAMETER'

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __reduce__(self):
        return 'MISSING_PARAMETER'


MISSING_PARAMETER = _MissingParameter()
