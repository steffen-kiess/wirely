import functools
import inspect
import typing
import os


def check_parameters(f):
    par_names = inspect.getfullargspec(f).args
    par_pos = {}
    for i, name in enumerate(par_names):
        par_pos[name] = i

    positional_types = {}
    kw_types = {}

    fun_name = f.__qualname__

    annotations = inspect.get_annotations(f)
    # print(par_names)
    for name in annotations:
        if name == 'return':
            continue

        ty = annotations[name]
        if isinstance(ty, type):
            def check_ty(value, is_pos, pos_name, ty=ty, ty_arg=None):
                if not isinstance(value, ty):
                    if is_pos:
                        raise Exception(f'Got incorrect type for positional argument {par_names[pos_name]} of {fun_name}: Got {type(value).__name__}, expected {ty.__name__}')
                    else:
                        raise Exception(f'Got incorrect type for kw argument {name} of {fun_name}: Got {type(value).__name__}, expected {ty.__name__}')
                # Force the value to have exactly that type
                if type(value) != ty and ty in [str, int, bool]:
                    return ty(value)
                else:
                    return value
        elif typing.get_origin(ty) == list:
            ty_arg, = typing.get_args(ty)
            def check_ty(value, is_pos, pos_name, ty=ty, ty_arg=ty_arg):
                res = []
                if not isinstance(value, list):
                    if is_pos:
                        raise Exception(f'Got incorrect type for positional argument {par_names[pos_name]} of {fun_name}: Got {type(value).__name__}, expected list')
                    else:
                        raise Exception(f'Got incorrect type for kw argument {name} of {fun_name}: Got {type(value).__name__}, expected list')
                for val in value:
                    if not isinstance(val, ty_arg):
                        raise Exception(f'Got incorrect type for value in list: Got {type(val).__name__}, expected {ty_arg.__name__!r}')
                    # TODO: Force type?
                    # val = ...(val)
                    res.append(val)
                return res
        elif typing.get_origin(ty) == typing.Union:
            ty_args = typing.get_args(ty)
            if len(ty_args) == 2 and ty_args[0] == type(None):
                ty_arg = ty_args[1]
            elif len(ty_args) == 2 and ty_args[1] == type(None):
                ty_arg = ty_args[0]
            else:
                raise Exception(f'Annotation {name!r} is not a type but a {type(ty).__name__}')
            def check_ty(value, is_pos, pos_name, ty=ty, ty_arg=ty_arg):
                if value is not None and not isinstance(value, ty_arg):
                    if is_pos:
                        raise Exception(f'Got incorrect type for positional argument {par_names[pos_name]} of {fun_name}: Got {type(value).__name__}, expected {ty}')
                    else:
                        raise Exception(f'Got incorrect type for kw argument {name} of {fun_name}: Got {type(value).__name__}, expected {ty}')
                # TODO: Force type?
                # if value is not None: value = ...(value)
                return value
        else:
            raise Exception(f'Annotation {name!r} is not a type but a {type(ty).__name__}')

        kw_types[name] = check_ty
        if name in par_pos:
            positional_types[par_pos[name]] = check_ty

    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        args = list(args)
        for i, value in enumerate(args):
            if i in positional_types:
                check_ty = positional_types[i]
                args[i] = check_ty(value, True, i)

        kwargs = dict(kwargs)
        for name in kwargs:
            if name in kw_types:
                check_type = kw_types[name]
                value = kwargs[name]
                kwargs[name] = check_type(value, False, name)

        return f(*args, **kwargs)
    return wrapper


def getEnvironmentBool(name, defVal):
    if type(defVal) != bool:
        raise Exception('type(defVal) != bool')

    if not name in os.environ:
        return defVal
    value = os.environ[name]
    s = value.lower()
    if s == '':
        return defVal
    elif s == '0' or s == 'false':
        return False
    elif s == '1' or s == 'true':
        return True
    else:
        raise Exception('Invalid value for environment variant %s: %s' % (repr(name), repr(value)))
