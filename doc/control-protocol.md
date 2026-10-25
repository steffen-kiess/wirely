# Wirely control protocol

This proposal defines a protocol for inspecting and controlling a running
Wirely runtime: querying status, pausing execution, dumping state and
temporaries, and replacing the program. It works over TCP, serial lines, and
KNX. The current protocol version is 0, an unstable development version.

The protocol suits slow, half-duplex links as well as fast links with high
latency. On slow links, a client sends one request at a time and waits for
its response. On reliable transports, it can send several requests before
the responses arrive. The runtime never sends unsolicited messages.

## Messages

A message is a 3-byte header, a body, and an optional authentication tag.
Each transport carries one message per frame, so the message length comes
from the frame.

| Offset | Field | Type | Meaning |
| --- | --- | --- | --- |
| 0 | `Kind` | `uint8` | 0 = request, 1 = response, 2 = error, 3 = notification, 4 = acknowledgement |
| 1 | `Seq` | `uint8` | Sequence number, see [Sequencing](#sequencing) |
| 2 | `Op` | `uint8` | Operation; responses and errors repeat it |

The body is one CBOR data item (RFC 8949), normally a map with small
unsigned integer keys. An empty body is equivalent to an empty map.
Receivers ignore unknown keys. Bodies use only integers, booleans,
byte strings, text strings, arrays, and maps; notifications also use null.

In an authenticated session, an 8-byte tag follows the body. See
[Authentication](#authentication).

The header and the `Hello` operation stay the same in all protocol
versions. `Hello` selects the version for the rest of the session.

### Sizes

Both peers accept messages of at least 64 bytes. `Hello` exchanges each
peer's limit; afterwards, each side sends messages up to the other side's
limit. Clients choose chunk and read lengths so that responses fit.

### Errors

An error response has `Kind` 2 and the body
`{1: code, 2: message text, 3: reason, 4: detail}`. The text is optional
and runtimes may omit it on slow links. The reason, an integer, and the
detail, any value with data about the failure, are optional; the operation
defines both.

| Code | Name | Meaning |
| --- | --- | --- |
| 1 | `Malformed` | The message cannot be decoded |
| 2 | `UnsupportedVersion` | No common protocol version |
| 3 | `UnknownOp` | Unknown or unsupported operation |
| 4 | `NoSession` | The runtime has no session; send `Hello` |
| 5 | `BadSequence` | Unexpected `Seq` |
| 6 | `Unauthenticated` | Missing or invalid tag |
| 7 | `InvalidArgument` | A body field is missing or invalid |
| 8 | `WrongState` | The operation is invalid in the current run state |
| 9 | `NotFound` | Unknown snapshot, upload, program, or transition |
| 10 | `TooLarge` | The response would exceed the client's limit |
| 11 | `IntegrityFailure` | The uploaded data does not match its digest |
| 12 | `ProgramRejected` | The runtime cannot load the uploaded program |
| 13 | `TransitionFailed` | The state transition procedure failed |
| 14 | `Internal` | The runtime failed to handle the request |
| 15 | `NotBound` | The session is not bound to the active program |
| 16 | `CommandFailed` | A native command could not be carried out |

## Sessions

A session starts with `Hello` and lasts until the next `Hello` or until
the transport connection closes. Serial links have no connections, so a
session lasts until the next `Hello` or until the runtime restarts.

Snapshots, uploads, and the run state belong to the runtime, not to a
session. A client can resume an upload after reconnecting. A
[program binding](#bindprogram) belongs to the session; `Hello` clears it.

### Sequencing

The session has a 32-bit request counter. It is 0 for `Hello` and goes up
by one with each new request. `Seq` holds its low 8 bits.

The client may have as many requests outstanding as the runtime's window,
which `Hello` reports. Windows are at most 256, so the `Seq` values of
outstanding requests are distinct. The runtime handles requests in order
and responds in order.

On transports that can lose messages, the window is 1. If a client gets no
response before its timeout, it sends the identical request again. The
runtime remembers the last request it handled and its response:

- A request with the next `Seq` is handled as a new request.
- A request with the last `Seq` gets the stored response again and is not
  run a second time.
- A `Hello` with the client nonce of the current session gets the stored
  `Hello` response again.
- Any other request is answered with `BadSequence`.

Instead of storing a `ReadSnapshot` or `ReadProgram` response, a runtime
may produce it again, since the result is the same. Clients discard
responses whose `Seq` does not match an outstanding request.

## Operations

| Op | Name | Auth | Run state |
| --- | --- | --- | --- |
| `0x00` | `Hello` | No | Any |
| `0x01` | `Status` | Yes | Any |
| `0x02` | `Pause` | Yes | Running or paused |
| `0x03` | `Resume` | Yes | Running or paused |
| `0x04` | `Step` | Yes | Paused |
| `0x05` | `BindProgram` | Yes | Running or paused |
| `0x10` | `Snapshot` | Yes | Running or paused |
| `0x11` | `ReadSnapshot` | Yes | Any |
| `0x12` | `ReleaseSnapshot` | Yes | Any |
| `0x20` | `UploadBegin` | Yes | Any |
| `0x21` | `UploadChunk` | Yes | Any |
| `0x22` | `UploadFinish` | Yes | Any |
| `0x23` | `Activate` | Yes | Any |
| `0x24` | `ReadProgram` | Yes | Any |
| `0x30` | `NativeCommand` | Yes | Running or paused |

Operations `0x80` through `0xff` are reserved for runtime-specific
extensions.

### Program identity

Programs are identified by their
[Build ID](bytecode-format.md#buildidsha512). Clients use it to find the
matching unstripped program file, whose symbol sections map names to
variables.

Messages carry a prefix of the build ID. A prefix in a request has 0 to 64
bytes and matches every program whose build ID starts with it; the empty
prefix matches any program. Prefixes in responses have at least 8 bytes;
this runtime sends 16.

### Hello

Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Largest message the client accepts |
| 2 | bytes(8) | Client nonce |
| 3 | array of uint | Protocol versions the client supports |

Response:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Largest message the runtime accepts |
| 2 | bytes(8) | Runtime nonce |
| 3 | uint | Selected protocol version |
| 4 | bool | Whether the session uses authentication tags |
| 5 | uint | Window, default 1 |
| 6 | uint | Largest zlib window size in bits for uploads, default 15 |
| 7 | text | Runtime name, optional |
| 8 | array of uint | Supported operations, optional; default all |

Nonces must not repeat for the same key. They can be random or derived
from a counter kept in persistent storage.

### Status

Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Mask of the response keys to send, default 6 (keys 1 and 2) |
| 2 | bool | Subscribe to changes, or with false cancel the subscription |

Bit *n* of the mask selects key *n*. The response contains the selected
keys that have values. With key 2 true, the session subscribes to changes
of the selected keys, replacing an earlier `Status` subscription; see
[Notifications](#notifications). Without key 2, the subscription stays
unchanged.

Response:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Run state: 0 running, 1 paused, 2 no program, 3 failed |
| 2 | uint | Number of steps completed by the active program |
| 3 | bytes | Build ID prefix of the active program, if any |
| 4 | bytes | Build ID prefix of the staged program, if any |
| 5 | text | Last error, if any |

### Notifications

A session can subscribe to changes, currently with `Status`. The runtime
then sends notifications: messages with `Kind` 3, the `Op` of the
subscribing request, and a body in the format of its response that
contains only the keys that changed since the last acknowledged
notification, or since the subscribing response. `null` means that a key
no longer has a value.

Notifications have their own 32-bit counter, which goes up by one with
each notification; `Seq` holds its low 8 bits. They do not count against the
window and are not ordered with responses.

The client acknowledges each notification with `Kind` 4 and the
notification's `Op` and `Seq`. The body is empty, or `{1: true}` to also
cancel the subscription. The runtime sends at most one unacknowledged
notification per session and ignores acknowledgements of other
notifications. It may delay notifications and merge changes into one.

On transports that can lose messages, the runtime sends a new
notification with the next `Seq` if it receives no acknowledgement before
its timeout. Because the client may have applied the unacknowledged
notification, the new one contains every key whose value differs from
either state.

A subscription ends when it is cancelled, with the session's next `Hello`,
or when the connection closes.

### Pause, Resume, and Step

`Pause` stops the runtime from starting further steps. The runtime responds
once no step is running. While paused, native tasks keep running. Triggers
and due wakeups are handled by the next step. `Resume` continues normal
execution. Both respond with the `Status` fields 1 and 2.

`Step` runs steps while paused. Request `{1: count}`, where `count`
defaults to 1. Response `{1: steps completed, 2: procedures run}`.

### BindProgram

`BindProgram` binds the session to the active program. Operations that
refer to program entities by index or offset require a bound session, and
otherwise fail with `NotBound`: `Snapshot`, `NativeCommand`, and
`ReadProgram` of the active program. In a bound session, `Activate` also
requires that the bound program is still active.

Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | bytes | Build ID prefix of the expected program, optional |

With key 1, the runtime binds the session only if the active program
matches the prefix and responds with `{}`. Without key 1, it binds the
session to the active program and responds with `{1: build ID prefix}`.
It answers `NotBound` when the prefix does not match, when no program is
active, or while programs are switching. A failed `BindProgram` leaves the
session unbound.

A binding lasts until the session's next `Hello` or until any `Activate`,
including one that starts the same program again. Binding again replaces
the binding. After `NotBound`, clients check `Status` and bind again if
the program they expect is active.

### Snapshots

A snapshot is a consistent view of the runtime buffers between two steps.
Bank 0 is the state written by the last step. Bank 1 holds the
temporaries of the last step, including values of skipped procedures. Both
use the
[buffer layout](bytecode-format.md#fixedvalues-statevariables-and-temporaries)
of the active program. Snapshots do not include native state, whose
representation depends on the runtime.

`Snapshot` takes a snapshot of the program the session is bound to.
Response:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Snapshot ID |
| 2 | uint | Steps completed when the snapshot was taken |
| 3 | array of uint | Byte size of each bank |

`ReadSnapshot` reads byte ranges from one bank. Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Snapshot ID |
| 2 | uint | Bank |
| 3 | array of [offset, length] | Byte ranges |

The response `{1: bytes}` contains the ranges concatenated in request
order. To read single variables, request their offsets and storage sizes;
to dump a whole bank, read it in consecutive chunks.

`ReleaseSnapshot` with `{1: snapshot ID}` frees a snapshot. Reading a
released or discarded snapshot fails with `NotFound`. Activating a program
discards all snapshots.

A runtime may copy the buffers for a snapshot and discard the least
recently used copy when it needs memory. Alternatively, while paused, it
may use the live buffers and discard the snapshot when the next step
runs. A runtime that does not copy answers `Snapshot` with `WrongState`
while running.

### Program upload

A runtime holds at most one upload and one staged program. Uploads
transfer a file in the [binary program format](bytecode-format.md). Symbol
sections may be stripped.

`UploadBegin` request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Transferred length in bytes |
| 2 | uint | Encoding: 0 raw, 1 zlib (RFC 1950) |
| 3 | bytes(32) | SHA-256 of the decoded program file |

zlib data must not use a larger window than the runtime accepts.

If the length, encoding, and digest match an unfinished upload, the
runtime continues it. Otherwise it discards any unfinished upload and
starts a new one. The response `{1: next offset}` tells the client where to
continue.

`UploadChunk` request `{1: offset, 2: data}`, with offsets into the
transferred bytes. The runtime stores the part of `data` beyond the next
offset and ignores bytes before it. It ignores chunks that start after the
next offset. The response is `{1: next offset}`.

`UploadFinish` checks the length and digest, decodes and validates the
program, and makes it the staged program, replacing any earlier one. The
response is `{1: build ID prefix}`. Failures are reported as
`IntegrityFailure` or `ProgramRejected`; the upload is then discarded.

### Activate

Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | bytes | Build ID prefix of the staged program |
| 2 | bool | Start paused, default false |
| 3 | bool | Keep the program after a runtime restart, default false |
| 4 | bool | Require a state transition, default false |

A program can contain state transition procedures, each for an old
program identified by its build ID. If the staged program has one for the
active program, the runtime pauses the active program and runs the
transition on its state and native state. Otherwise the staged program
starts with its initial state; if key 4 is true, the runtime instead
answers `NotFound` and keeps the active program.

In a bound session, the runtime answers `NotBound` unless the bound
program is still active. Activating a program clears the bindings of all
sessions.

The runtime checks these conditions before responding and then switches
programs. If the transition or the start of the new program fails, the
runtime returns to the previous program and records the error. Clients use
`Status` to check the result.

### ReadProgram

`ReadProgram` downloads the active or staged program, for example to
prepare a state transition. Reading the active program requires a session
bound to it. Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | 0 active, 1 staged |
| 2 | bytes | Build ID prefix of the expected staged program |
| 3 | uint | Offset |
| 4 | uint | Length |

Key 2 is required for the staged program and not used for the active one.
The response is `{1: bytes, 2: file length}`. If the staged program does
not match the prefix, the runtime answers `NotFound`.

The runtime returns the program as it stores it, which may lack symbol
sections. To match variables by name, clients need the unstripped file
with the same build ID.

### NativeCommand

`NativeCommand` sends a command to the native state that an initializer of
a library created, for example to set the value of a
`Std.DebugValueSource`. Libraries number and document their commands;
negative numbers are reserved for commands common to all native states.
Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Index of the initializer in `ProcedureInstances` |
| 2 | int | Command |
| 3 | any | Argument, optional |

The response is `{1: result}`, without key 1 if the command has no result.
The runtime runs commands between steps; their effects show in later
steps. It answers `NotFound` if the procedure instance has no native state
or the command is unknown. Commands report rejected arguments as
`InvalidArgument` and other failures as `CommandFailed`, both optionally
with a reason and a detail that the command defines. When an argument,
result, or detail is a map, its negative keys are reserved for meanings
common to all native states, like negative command numbers.

## Authentication

Authentication uses a pre-shared 32-byte key per runtime. The runtime's
configuration decides which transports require it.

In an authenticated session, every message after `Hello` ends with the
first 8 bytes of

```
HMAC-SHA256(key, direction || client nonce || runtime nonce
                 || counter || header || body)
```

`direction` is one byte: 0 for requests, 1 for responses and errors, 2 for
notifications, and 3 for acknowledgements. `counter` is a little-endian
`uint32`: the request counter for requests, the counter of their request
for responses and errors, the notification counter for notifications, and
the counter of their notification for acknowledgements. Receivers discard
messages with invalid tags, and the runtime answers them with
`Unauthenticated` without a tag.

Tags protect against modified, forged, and replayed messages, but the
protocol does not encrypt messages. Use TLS where state values are
confidential. Runtimes can omit tags on TLS connections that authenticate
the client.

## Transports

### TCP

A client starts a plain connection by sending the 4 bytes `WRLC`. Each
frame is then a little-endian `uint16` message length followed by the
message.

A TLS connection carries the same bytes inside TLS. Since TLS connections
start with byte `0x16`, a runtime can accept both on one port by looking
at the first byte. Clients that require TLS must not fall back to a plain
connection.

TCP is reliable, so runtimes should offer a window larger than 1 and
message limits of several kilobytes.

### Serial

Each frame is the message followed by its CRC-32 (ISO-HDLC, as computed by
zlib), little-endian, encoded with COBS and terminated by a zero byte.
Receivers discard frames with an invalid CRC, and clients retransmit after
their timeout. Sessions should use message limits of about 256 bytes.

### KNX

The KNX transport will be specified later. Working notes, not final:

- Messages are sent connectionless to the runtime's individual address.
  Group addresses would reach every device on the line, and the
  connection-oriented mode adds an acknowledgement frame per frame, while
  this protocol already sequences and retransmits messages.
- Messages that do not fit into one frame are split into fragments, since
  KNX does not split long messages and a standard TP1 frame carries only
  about 14 bytes. Each fragment starts with one byte: a "last fragment"
  bit and a 7-bit index. Index 0 starts a new message; `Seq` already
  detects outdated messages, so fragments need no message number.
- A CRC over the reassembled message detects corruption, since the frame
  checksum is weak, and fragments of different messages that got mixed.
  Single-frame messages might omit it, because the CRC alone can push a
  small message into a second frame.
- Extended frames, where the installation supports them, reduce the
  number of fragments. On TP1, each frame costs about 20 ms beyond its
  data.

## Local sockets

`wirely run-service --state-directory DIR` creates two Unix domain sockets
in `DIR`, accessible only to the user running the service:

- `control` uses the TCP framing and accepts plain and TLS connections.
  TLS requires `--control-tls-cert` and `--control-tls-key`.
- `control-serial` uses the serial framing; each connection is a separate
  line.

While running, the service holds a write lock on `DIR/lock`, so only one
runtime uses a state directory at a time.

Neither socket uses authentication tags. The service currently supports
`Hello`, `Status`, `Pause`, `Resume`, `Step`, `BindProgram`, the
snapshot operations, and `NativeCommand`.

`wirely client` sends requests to these sockets, for example:

```sh
wirely client --unix-path DIR/control --program program.wrl dump
wirely client --unix-path DIR/control-serial --protocol serial pause
```

`--program` names the `.wrl` files of the running program; repeat it for
several files. The client compiles them once and binds to that program,
so commands can refer to names.

`wirely client --unix-path DIR/control watch` prints the run state and
program whenever they change, until it is interrupted.

`call INSTANCE COMMAND [ARGUMENT]` sends a native command. Argument and
result use CBOR diagnostic notation: integers, `h'…'` byte strings, text
strings, `true`, `false`, `null`, arrays, and maps. Quote arguments for the
shell and in interactive mode, as in `call 0 1 "{1: h'0700'}"`.

With `--program`, `get NODE`, `set NODE VALUE`, and `send NODE VALUE` use
the commands of debug source nodes by name, with values in the
[text format](value-types.md#text-format): `set` and `get` for a
`Std.DebugValueSource`, and `send` and `get`, for the queued event, for a
`Std.DebugEventSource`.

Without a command, `wirely client` reads commands with the same syntax,
one per line, until `quit` or end of input. On a terminal, it offers line
editing, history, and completion; otherwise it stops at the first failing
command.

Use `--tls` for TLS connections, with `--tls-ca` and `--tls-server-name`
to verify the certificate.

## Open questions

- Whether the runtime should report variable changes, for example by
  holding a request until watched byte ranges change.
