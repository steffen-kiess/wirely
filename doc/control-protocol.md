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
| 0 | `Kind` | `uint8` | 0 = request, 1 = response, 2 = error |
| 1 | `Seq` | `uint8` | Sequence number, see [Sequencing](#sequencing) |
| 2 | `Op` | `uint8` | Operation; responses and errors repeat it |

The body is one CBOR data item (RFC 8949), normally a map with small
unsigned integer keys. An empty body is equivalent to an empty map.
Receivers ignore unknown keys. Bodies use only unsigned integers, booleans,
byte strings, text strings, arrays, and maps.

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
`{1: code, 2: message text}`. The text is optional and runtimes may omit
it on slow links.

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

## Sessions

A session starts with `Hello` and lasts until the next `Hello` or until
the transport connection closes. Serial links have no connections, so a
session lasts until the next `Hello` or until the runtime restarts.

Snapshots, uploads, and the run state belong to the runtime, not to a
session. A client can resume an upload after reconnecting.

### Sequencing

The session has a 32-bit request counter. It is 0 for `Hello` and goes up
by one with each new request. `Seq` holds its low 8 bits.

The client may have as many requests outstanding as the runtime's window,
which `Hello` reports. The runtime handles requests in order and responds
in order.

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
| `0x10` | `Snapshot` | Yes | Running or paused |
| `0x11` | `ReadSnapshot` | Yes | Any |
| `0x12` | `ReleaseSnapshot` | Yes | Any |
| `0x20` | `UploadBegin` | Yes | Any |
| `0x21` | `UploadChunk` | Yes | Any |
| `0x22` | `UploadFinish` | Yes | Any |
| `0x23` | `Activate` | Yes | Any |
| `0x24` | `ReadProgram` | Yes | Any |

Operations `0x80` through `0xff` are reserved for runtime-specific
extensions.

### Program identity

Programs are identified by their short build ID, the first 16 bytes of
the [Build ID](bytecode-format.md#buildidsha512). Clients use it to
find the matching unstripped program file, whose symbol sections map
names to variables.

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

Response:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Run state: 0 running, 1 paused, 2 no program, 3 failed |
| 2 | uint | Number of steps completed by the active program |
| 3 | bytes(16) | Short build ID of the active program, if any |
| 4 | bytes(16) | Short build ID of the staged program, if any |
| 5 | text | Last error, if any |

### Pause, Resume, and Step

`Pause` stops the runtime from starting further steps. The runtime responds
once no step is running. While paused, native tasks keep running. Triggers
and due wakeups are handled by the next step. `Resume` continues normal
execution. Both respond with the `Status` fields 1 and 2.

`Step` runs steps while paused. Request `{1: count}`, where `count`
defaults to 1. Response `{1: steps completed, 2: procedures run}`.

### Snapshots

A snapshot is a consistent view of the runtime buffers between two steps.
Bank 0 is the state written by the last step. Bank 1 holds the
temporaries of the last step, including values of skipped procedures. Both
use the
[buffer layout](bytecode-format.md#loadvalues-statevariables-and-temporaries)
of the active program. Snapshots do not include native state, whose
representation depends on the runtime.

`Snapshot` takes a snapshot. Response:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | Snapshot ID |
| 2 | uint | Steps completed when the snapshot was taken |
| 3 | bytes(16) | Short build ID of the program |
| 4 | array of uint | Byte size of each bank |

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
response is `{1: short build ID}`. Failures are reported as
`IntegrityFailure` or `ProgramRejected`; the upload is then discarded.

### Activate

Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | bytes(16) | Short build ID of the staged program |
| 2 | bool | Start paused, default false |
| 3 | bool | Keep the program after a runtime restart, default false |
| 4 | bool | Require a state transition, default false |

A program can contain state transition procedures, each for an old
program identified by its build ID. If the staged program has one for the
active program, the runtime pauses the active program and runs the
transition on its state and native state. Otherwise the staged program
starts with its initial state; if key 4 is true, the runtime instead
answers `NotFound` and keeps the active program.

The runtime checks these conditions before responding and then switches
programs. If the transition or the start of the new program fails, the
runtime returns to the previous program and records the error. Clients use
`Status` to check the result.

### ReadProgram

`ReadProgram` downloads the active or staged program, for example to
prepare a state transition. Request:

| Key | Type | Meaning |
| --- | --- | --- |
| 1 | uint | 0 active, 1 staged |
| 2 | bytes(16) | Short build ID of the expected program |
| 3 | uint | Offset |
| 4 | uint | Length |

The response is `{1: bytes, 2: file length}`. If the program does not have
the expected build ID, the runtime answers `NotFound`.

The runtime returns the program as it stores it, which may lack symbol
sections. To match variables by name, clients need the unstripped file
with the same build ID.

## Authentication

Authentication uses a pre-shared 32-byte key per runtime. The runtime's
configuration decides which transports require it.

In an authenticated session, every message after `Hello` ends with the
first 8 bytes of

```
HMAC-SHA256(key, direction || client nonce || runtime nonce
                 || counter || header || body)
```

`direction` is one byte: 0 for requests, 1 for responses and errors.
`counter` is the request counter as a little-endian `uint32`. Responses use
the counter of their request. Receivers discard messages with invalid tags,
and the runtime answers them with `Unauthenticated` without a tag.

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

The KNX transport will be specified later.

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
`Hello`, `Status`, `Pause`, `Resume`, `Step`, and the snapshot operations.

`wirely client` sends requests to these sockets, for example:

```sh
wirely client --unix-path DIR/control dump --program program.wrl
wirely client --unix-path DIR/control-serial --protocol serial pause
```

Use `--tls` for TLS connections, with `--tls-ca` and `--tls-server-name`
to verify the certificate.

## Open questions

- Whether the runtime should report variable changes, for example by
  holding a request until watched byte ranges change.
