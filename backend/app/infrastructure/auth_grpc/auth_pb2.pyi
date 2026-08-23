from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ValidateRequest(_message.Message):
    __slots__ = ("session_id", "caller_host")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_HOST_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    caller_host: str
    def __init__(self, session_id: _Optional[str] = ..., caller_host: _Optional[str] = ...) -> None: ...

class ValidateResponse(_message.Message):
    __slots__ = ("fields",)
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    fields: _containers.ScalarMap[str, str]
    def __init__(self, fields: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ListUsersRequest(_message.Message):
    __slots__ = ("session_id", "caller_host")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    CALLER_HOST_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    caller_host: str
    def __init__(self, session_id: _Optional[str] = ..., caller_host: _Optional[str] = ...) -> None: ...

class ListUser(_message.Message):
    __slots__ = ("fields",)
    class FieldsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    FIELDS_FIELD_NUMBER: _ClassVar[int]
    fields: _containers.ScalarMap[str, str]
    def __init__(self, fields: _Optional[_Mapping[str, str]] = ...) -> None: ...

class ListUsersResponse(_message.Message):
    __slots__ = ("users",)
    USERS_FIELD_NUMBER: _ClassVar[int]
    users: _containers.RepeatedCompositeFieldContainer[ListUser]
    def __init__(self, users: _Optional[_Iterable[_Union[ListUser, _Mapping]]] = ...) -> None: ...
