import os
import argparse
import traceback
import subprocess
from json import dumps
from typing import Union, Any
from pprint import pprint
from collections import ChainMap

from pycparser import c_ast, parse_file


_QUICKJS_FFI_WRAP_PTR_FUNC_DECL = '''
const __quickjs_ffi_wrap_ptr_func_decl = (lib, name, nargs, ...types) => {
    // wrap C function
    const c_types = types.map(type => {
        if (typeof type == 'string') {
            return type;
        } else if (typeof type == 'object') {
            if (type.kind == 'PtrFuncDecl') {
                return 'pointer';
            } else {
                throw new Error('Unsupported type');
            }
        } else {
            throw new Error('Unsupported type');
        }
    });

    let c_func;

    try {
        c_func = new CFunction(lib, name, nargs, ...c_types);
    } catch (e) {
        c_func = undefined;
    }
    
    const js_func = (...js_args) => {
        const c_args = types.slice(1).map((type, i) => {
            const js_arg = js_args[i];

            if (typeof type == 'string') {
                return js_arg;
            } else if (typeof type == 'object') {
                if (type.kind == 'PtrFuncDecl') {
                    const c_cb = new CCallback(js_arg, null, ...type.types);
                    return c_cb.cfuncptr;
                } else {
                    throw new Error('Unsupported type');
                }
            } else {
                throw new Error('Unsupported type');
            }
        });

        return c_func.invoke(...c_args);
    };

    return js_func;
};

const _quickjs_ffi_wrap_ptr_func_decl = (lib, name, nargs, ...types) => {
    try {
        return __quickjs_ffi_wrap_ptr_func_decl(lib, name, nargs, ...types);
    } catch (e) {
        return undefined;
    }
};
'''

PRIMITIVE_C_TYPES_NAMES = [
    'void',
    'uint8',
    'sint8',
    'uint16',
    'sint16',
    'uint32',
    'sint32',
    'uint64',
    'sint64',
    'float',
    'double',
    'uchar',
    'schar',
    'ushort',
    'sshort',
    'uint',
    'sint',
    'ulong',
    'slong',
    'longdouble',
    'pointer',
    'complex_float',
    'complex_double',
    'complex_longdouble',
    'uint8_t',
    'int8_t',
    'uint16_t',
    'int16_t',
    'uint32_t',
    'int32_t',
    'char',
    'short',
    'int',
    'long',
    'string',
    'uintptr_t',
    'intptr_t',
    'size_t',
]

PRIMITIVE_C_TYPES = {
    **{n: n for n in PRIMITIVE_C_TYPES_NAMES},
    '_Bool': 'int',
    'signed char': 'schar',
    'unsigned char': 'uchar',
    'signed': 'sint',
    'signed int': 'sint',
    'unsigned': 'uint',
    'unsigned int': 'uint',
    'long long': 'sint64', # FIXME: platform specific
    'signed long': 'uint32', # FIXME: platform specific
    'unsigned long': 'uint32', # FIXME: platform specific
    'signed long long': 'sint64', # FIXME: platform specific
    'unsigned long long': 'uint64', # FIXME: platform specific
    'long double': 'longdouble',
}

USER_DEFINED_TYPE_DECL = {}
USER_DEFINED_FUNC_DECL = {}
USER_DEFINED_PTR_FUNC_DECL = {} # ???
USER_DEFINED_STRUCT_DECL = {}
USER_DEFINED_ARRAY_DECL = {}
USER_DEFINED_ENUM_DECL = {}

USER_DEFINED_TYPEDEF_STRUCT = {}
USER_DEFINED_TYPEDEF_FUNC_DECL = {}
USER_DEFINED_TYPEDEF_PTR_DECL = {}

USER_DEFINED_DECL = ChainMap(
    USER_DEFINED_TYPE_DECL,
    USER_DEFINED_FUNC_DECL,
    USER_DEFINED_PTR_FUNC_DECL, # ???
    USER_DEFINED_STRUCT_DECL,
    USER_DEFINED_ARRAY_DECL,
    USER_DEFINED_ENUM_DECL,
)

USER_DEFINED_TYPEDEF = ChainMap(
    USER_DEFINED_TYPEDEF_STRUCT,
    USER_DEFINED_TYPEDEF_FUNC_DECL,
    USER_DEFINED_TYPEDEF_PTR_DECL,
)

USER_DEFINED_TYPES = ChainMap(
    USER_DEFINED_DECL,
    USER_DEFINED_TYPEDEF,
)

TYPES = ChainMap(
    PRIMITIVE_C_TYPES,
    USER_DEFINED_TYPES,
)

CType = Union[str, dict]
JsTypeLine = (CType, str)


def get_leaf_node(n):
    if hasattr(n, 'type'):
        return get_leaf_node(n.type)
    else:
        return n


def get_leaf_name(n) -> list[str]:
    if isinstance(n, c_ast.IdentifierType):
        if hasattr(n, 'names'):
            return ' '.join(n.names)
        else:
            return ''
    else:
        return get_leaf_names(n.type)


def get_typename(n, func_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'
    return js_type, js_line


def get_type_decl(n, typedef=None, decl=None, func_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'

    if typedef:
        js_name: str | None = typedef.name

        if isinstance(n.type, c_ast.Struct):
            t, _ = get_struct(n.type, typedef=typedef, type_decl=n)
            
            js_type = {
                'kind': 'TypeDecl',
                'name': js_name,
                'type': t,
            }

            if js_name:
                USER_DEFINED_TYPEDEF_STRUCT[js_name] = js_type

            js_line = f'type_decl struct: {dumps(js_type)}'
        else:
            raise TypeError(n)
    elif decl or func_decl:
        if isinstance(n.type, c_ast.Enum):
            t, l = get_enum(n.type, type_decl=n)
            js_name: str = n.declname

            js_type = {
                'kind': 'TypeDecl',
                'name': js_name,
                'type': t,
            }

            js_line = f'export const {js_name} = {dumps(js_type["type"]["items"])};'
            USER_DEFINED_TYPE_DECL[js_name] = js_type
        elif isinstance(n.type, c_ast.PtrDecl):
            t, l = get_ptr_decl(n.type, decl=decl, func_decl=func_decl)

            js_type = {
                'kind': 'PtrFuncDecl',
                'name': decl.name,
                'type': t,
            }

            js_line = f'/* type_decl ptr_decl: {l} */'
        elif isinstance(n.type, c_ast.IdentifierType):
            name: str = get_leaf_name(n.type)
            js_type = name
        else:
            raise TypeError(n)
    else:
        raise TypeError(n)

    if js_name:
        USER_DEFINED_TYPE_DECL[js_name] = js_type

    return js_type, js_line


def get_ptr_decl(n, decl=None, func_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'

    if decl and func_decl:
        t, l = get_node(n.type, decl=decl, func_decl=func_decl)

        js_type = {
            'kind': 'PtrFuncDecl',
            'name': decl.name,
            'type': t,
        }
    elif decl:
        pass
    elif func_decl:
        pass
    else:
        raise TypeError(type(n))
    # raise TypeError(type(n))
    
    return js_type, js_line


def get_struct(n, typedef=None, type_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'
    js_name: str
    js_fields: dict
    
    if n.name:
        js_name = n.name
    elif type_decl and type_decl.declname:
        js_name = type_decl.declname
    elif typedef and typedef.name:
        js_name = typedef.name
    else:
        raise ValueError(f'Could not get name of struct node {n}')

    # NOTE: does not parse struct fields
    js_fields = {}

    js_type = {
        'kind': 'Struct',
        'name': js_name,
        'fields': js_fields,
    }

    if js_name:
        USER_DEFINED_STRUCT_DECL[js_name] = js_type

    js_line = f'struct: {dumps(js_type)}'
    return js_type, js_line


def get_enum(n, decl=None, type_decl=None) -> JsTypeLine:
    js_type: CType
    js_line: str

    if decl or type_decl:
        assert isinstance(n.values, c_ast.EnumeratorList)
        assert isinstance(n.values.enumerators, list)
        last_enum_field_value: int = -1

        js_type = {
            'kind': 'Enum',
            'name': n.name,
            'items': {},
        }

        for m in n.values.enumerators:
            enum_field_name: str = m.name
            enum_field_value: Any

            if m.value:
                if isinstance(m.value, c_ast.Constant):
                    enum_field_value = eval(m.value.value)
                elif m.value is None:
                    enum_field_value = None
                elif isinstance(m.value, c_ast.BinaryOp):
                    enum_field_value = eval(f'{m.value.left.value} {m.value.op} {m.value.right.value}')
                elif isinstance(m.value, c_ast.UnaryOp):
                    enum_field_value = eval(f'{m.value.op} {m.value.expr.value}')
                else:
                    raise TypeError(f'get_enum: Unsupported {type(m.value)}')
            else:
                enum_field_value = last_enum_field_value + 1
            
            last_enum_field_value = enum_field_value
            js_type['items'][enum_field_name] = enum_field_value

        USER_DEFINED_ENUM_DECL[js_type["name"]] = js_type
        js_line = f'enum: {dumps(js_type)}'
    else:
        raise TypeError(type(n))

    return js_type, js_line


def get_func_decl(n, typedef=None, decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'

    if typedef:
        raise TypeError(n)
    elif decl:
        assert isinstance(n.args, c_ast.ParamList)
        assert isinstance(n.args.params, list)
        
        js_type = {
            'kind': 'FuncDecl',
            'name': decl.name,
            'return_type': None,
            'params_types': [],
        }

        t, l = get_node(n.type, decl=decl, func_decl=n)
        js_type['return_type'] = t

        for m in n.args.params:
            t, l = get_node(m, func_decl=n)
            js_type['params_types'].append(t)

        js_line = f'/* {js_type["name"]}: {js_type["return_type"]} = {dumps(js_type["params_types"])} */'
        USER_DEFINED_FUNC_DECL[js_type['name']] = js_type
    else:
        raise TypeError(type(n))
    
    return js_type, js_line


# def get_enum_decl(n) -> JsTypeLine:
#     js_type: CType
#     js_line: str
#     raise TypeError(type(n))
#     return js_type, js_line


# def get_array_decl(n) -> JsTypeLine:
#     js_type: CType
#     js_line: str
#     raise TypeError(type(n.type))
#     return js_type, js_line


def get_typedef(n) -> JsTypeLine:
    js_type: CType
    js_line: str = '/* unset */'
    js_name: str = n.name

    if isinstance(n.type, c_ast.TypeDecl):
        t, _ = get_type_decl(n.type, typedef=n)
    elif isinstance(n.type, c_ast.FuncDecl):
        t, _ = get_func_decl(n.type, typedef=n)
    elif isinstance(n.type, c_ast.PtrDecl):
        # js_type, js_line = get_typedef_ptr_decl(n, n.type)
        raise TypeError(type(n.type))
    else:
        # js_line = f'/* get_typedef: Unsupported {type(n.type)} */'
        raise TypeError(type(n.type))

    js_type = {
        'kind': 'Typedef',
        'name': js_name,
        'type': t,
    }

    js_line = f'typedef: {dumps(js_type)}'
    return js_type, js_line


def get_decl(n, func_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'

    if isinstance(n.type, c_ast.Enum):
        js_type, js_line = get_enum(n.type, decl=n)
    elif isinstance(n.type, c_ast.TypeDecl):
        js_type, js_line = get_type_decl(n.type, decl=n)
    elif isinstance(n.type, c_ast.FuncDecl):
        js_type, js_line = get_func_decl(n.type, decl=n)
    elif isinstance(n.type, c_ast.PtrDecl):
        js_type, js_line = get_ptr_decl(n.type, decl=n)
    else:
        raise TypeError(type(n.type))
    
    return js_type, js_line


def get_node(n, decl=None, func_decl=None) -> JsTypeLine:
    js_type: CType = None
    js_line: str = '/* unset */'

    if decl:
        if isinstance(n, c_ast.TypeDecl):
            js_type, js_line = get_type_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.PtrDecl):
            js_type, js_line = get_ptr_decl(n, decl=decl, func_decl=func_decl)
        else:
            raise TypeError(n)
    elif func_decl:
        if isinstance(n, c_ast.Decl):
            js_type, js_line = get_decl(n, func_decl=func_decl)
        elif isinstance(n, c_ast.TypeDecl):
            js_type, js_line = get_type_decl(n, func_decl=func_decl)
        elif isinstance(n, c_ast.PtrDecl):
            js_type, js_line = get_ptr_decl(n, decl=decl, func_decl=func_decl)
        elif isinstance(n, c_ast.Typename):
            js_type, js_line = get_typename(n, func_decl=func_decl)
        else:
            raise TypeError(n)
    else:
        raise TypeError(n)

    return js_type, js_line


def get_file_ast(file_ast, shared_library: str) -> str:
    js_lines: list[str]
    js_type: CType = None
    js_line: str = '/* unset */'

    js_lines = [
        "import { CFunction, CCallback } from './quickjs-ffi.js';",
        f"const LIB = {dumps(shared_library)};",
        _QUICKJS_FFI_WRAP_PTR_FUNC_DECL,
    ]

    for n in file_ast.ext:
        print(n)

        if isinstance(n, c_ast.Typedef):
            js_type, js_line = get_typedef(n)
            # raise TypeError(type(n.type))
        elif isinstance(n, c_ast.Decl):
            js_type, js_line = get_decl(n)
        else:
            raise TypeError(type(n.type))

        js_lines.append(js_line)

    js_lines = '\n'.join(js_lines)
    return js_lines


def create_output_dir(output_path: str):
    dirpath, filename = os.path.split(output_path)
    os.makedirs(dirpath, exist_ok=True)


def preprocess_header_file(compiler: str, input_path: str, output_path: str):
    cmd = [compiler, '-E', input_path]
    output: bytes = subprocess.check_output(cmd)
    
    with open(output_path, 'w+b') as f:
        f.write(output)


def parse_and_convert(compiler: str, shared_library: str, input_path: str, output_path: str):
    # check existance of input_path
    assert os.path.exists(input_path)

    # create destination directory
    create_output_dir(output_path)

    # preprocess input header path
    dirpath, filename = os.path.split(output_path)
    basename, ext = os.path.splitext(filename)
    processed_output_path: str = os.path.join(dirpath, f'{basename}.h')
    preprocess_header_file(compiler, input_path, processed_output_path)

    # parse input header path
    file_ast = parse_file(processed_output_path, use_cpp=True)
    assert isinstance(file_ast, c_ast.FileAST)

    # wrap C code into JS
    try:
        output_data: str = get_file_ast(file_ast, shared_library=shared_library)
    except Exception as e:
        traceback.print_exc()
        output_data = ''

    print('-' * 20)
    print(output_data)

    with open(output_path, 'w+') as f:
        f.write(output_data)

    # pprint(TYPES, sort_dicts=False)
    print('USER_DEFINED_TYPE_DECL:')
    pprint(USER_DEFINED_TYPE_DECL, sort_dicts=False)
    print()

    print('USER_DEFINED_FUNC_DECL:')
    pprint(USER_DEFINED_FUNC_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_PTR_FUNC_DECL:')
    pprint(USER_DEFINED_PTR_FUNC_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_STRUCT_DECL:')
    pprint(USER_DEFINED_STRUCT_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_ARRAY_DECL:')
    pprint(USER_DEFINED_ARRAY_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_ENUM_DECL:')
    pprint(USER_DEFINED_ENUM_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_TYPEDEF_STRUCT:')
    pprint(USER_DEFINED_TYPEDEF_STRUCT, sort_dicts=False)
    print()
    
    print('USER_DEFINED_TYPEDEF_FUNC_DECL:')
    pprint(USER_DEFINED_TYPEDEF_FUNC_DECL, sort_dicts=False)
    print()
    
    print('USER_DEFINED_TYPEDEF_PTR_DECL:')
    pprint(USER_DEFINED_TYPEDEF_PTR_DECL, sort_dicts=False)
    print()


if __name__ == '__main__':
    # cli arg parser
    parser = argparse.ArgumentParser(description='Convert .h to .js')
    parser.add_argument('-c', dest='compiler', default='gcc', help='gcc, clang, tcc')
    parser.add_argument('-l', dest='shared_library', default='./libcfltk.so', help='Shared library')
    parser.add_argument('-i', dest='input_path', help='input .h path')
    parser.add_argument('-o', dest='output_path', help='output .js path')
    
    # parse_and_convert
    args = parser.parse_args()
    parse_and_convert(args.compiler, args.shared_library, args.input_path, args.output_path)
