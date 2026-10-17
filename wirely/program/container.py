"""Section container of the binary program format."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

_PREFIX = struct.Struct('<IHH')
_HEADER = struct.Struct('<IHHII')
_ENTRY = struct.Struct('<IIII')
_ALIGNMENT = 16
_UINT16_MAX = 0xffff
_UINT32_MAX = 0xffffffff


class ContainerError(ValueError):
    """A byte string is not a valid program file container."""


@dataclass(frozen=True, slots=True)
class Section:
    """One section: its type, record type, and contents."""

    section_type: int  # Section-type number.
    record_type: int  # Record-type number describing the layout of data.
    data: bytes  # Section contents, without padding.

    @property
    def length(self):
        """Section length in bytes."""
        return len(self.data)


@dataclass(slots=True)
class SectionList:
    """The version and sections of a program file, written sorted by section type."""

    MAGIC = 0x594c5257
    SUPPORTED_MAJOR = 0

    sections: list[Section] = field(default_factory=list)  # Sections in any order.
    version: tuple[int, int] = (SUPPORTED_MAJOR, 0)  # Major and minor format version.

    @classmethod
    def from_bytes(cls, data):
        """Parse a file, validating its header and section directory."""
        data = bytes(data)
        if len(data) < _PREFIX.size:
            raise ContainerError(f'File is shorter than the {_PREFIX.size}-byte identification prefix')
        magic, major, minor = _PREFIX.unpack_from(data)
        if magic != cls.MAGIC:
            raise ContainerError(f'Bad magic 0x{magic:08x}')
        if major != cls.SUPPORTED_MAJOR:
            raise ContainerError(f'Unsupported major version {major}')
        if len(data) < _HEADER.size:
            raise ContainerError(f'File is shorter than the {_HEADER.size}-byte header')
        _, _, _, file_length, section_count = _HEADER.unpack_from(data)
        if file_length != len(data):
            raise ContainerError(f'File length is {len(data)}, but the header says {file_length}')
        if _HEADER.size + section_count * _ENTRY.size > file_length:
            raise ContainerError(f'Directory of {section_count} entries extends past the end of the file')

        sections: list[Section] = []
        for index in range(section_count):
            offset, length, section_type, record_type = _ENTRY.unpack_from(
                data, _HEADER.size + index * _ENTRY.size)
            if offset % _ALIGNMENT != 0:
                raise ContainerError(f'Section {index} offset {offset} is not {_ALIGNMENT}-byte aligned')
            if offset + length > file_length:
                raise ContainerError(f'Section {index} extends past the end of the file')
            if sections and section_type <= sections[-1].section_type:
                raise ContainerError(f'Section types are not in strictly ascending order at section {index}')
            sections.append(Section(section_type, record_type, data[offset:offset + length]))
        return cls(sections, (major, minor))

    @classmethod
    def load(cls, path):
        """Read and parse the file at path."""
        with open(path, 'rb') as file:
            return cls.from_bytes(file.read())

    def to_bytes(self):
        """Serialize sorted by section type, with sections zero-padded to 16-byte alignment."""
        major, minor = self.version
        if not (0 <= major <= _UINT16_MAX and 0 <= minor <= _UINT16_MAX):
            raise ValueError(f'Version {major}.{minor} does not fit the header')
        sections = sorted(self.sections, key=lambda section: section.section_type)
        for index, section in enumerate(sections):
            for value in (section.section_type, section.record_type):
                if not 0 <= value <= _UINT32_MAX:
                    raise ValueError(f'Type number {value} does not fit uint32')
            if index > 0 and section.section_type == sections[index - 1].section_type:
                raise ValueError(f'Section type 0x{section.section_type:08x} occurs more than once')

        directory = []
        offset = _HEADER.size + len(sections) * _ENTRY.size
        for section in sections:
            offset += -offset % _ALIGNMENT
            directory.append(offset)
            offset += section.length
        file_length = offset
        if file_length > _UINT32_MAX:
            raise ValueError(f'File length {file_length} does not fit uint32')

        output = bytearray(_HEADER.pack(self.MAGIC, major, minor, file_length, len(sections)))
        for section, section_offset in zip(sections, directory):
            output += _ENTRY.pack(section_offset, section.length, section.section_type, section.record_type)
        for section, section_offset in zip(sections, directory):
            output += bytes(section_offset - len(output))
            output += section.data
        return bytes(output)

    def save(self, path):
        """Write the serialized file to path."""
        with open(path, 'wb') as file:
            file.write(self.to_bytes())
