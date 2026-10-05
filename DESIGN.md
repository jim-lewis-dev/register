# Design decisions

Register gives a name to some bytes and a few properties. Its job is to preserve that value, retrieve it, and optionally execute it. Other programs interpret JSON, process images, or transform text. Keeping those responsibilities outside Register makes the interface useful in ordinary Unix pipelines.

## One value, one transaction

Each SQLite row holds a name, content BLOB, executable flag, text flag, and MIME type. Updating or copying a register changes its content and properties in one transaction. Input is read and inspected before the write transaction, so a failed file or clipboard read leaves the previous value intact. Updates and copies acquire a write transaction before reading the properties they will preserve.

The earlier format used separate content files and JSON metadata. SQLite removes the need to coordinate two independent writes. First use publishes a fully initialized database without replacing a concurrent creator's database. Legacy migration builds a complete replacement before removing imported files.

Transactions cover individual register operations, not an entire shell pipeline. Empty input is valid data, including when it comes from an upstream command that produced nothing. Buffers are read into memory; this is a personal buffer utility, not a streaming archive for very large files.

**Evidence:** the [CLI tests](tests/test_register.py) check failed writes and reads, concurrent first use, copy replacement, complete migration, and preservation of an invalid or incomplete legacy store.

## Bytes first; execution is a separate action

`get` returns exactly the stored bytes, with no decoding or added newline. MIME and text detection provide properties without changing content. This lets a JSON array, a PNG, and a NUL-separated file list share the same storage and retrieval path.

Saving content never runs it. `run` requires the executable flag, materializes a temporary snapshot, and starts a child process. Native programs and shebang scripts use their normal interpreter behavior; commands without a shebang fall back to Bash. Argument boundaries, working directory, standard streams, and exit status are preserved. Execution uses the caller's permissions; it is not a sandbox, and a child cannot change the parent shell's directory.

**Evidence:** the [CLI tests](tests/test_register.py) compare binary bytes, reject execution without permission, exercise Bash and native programs, and verify arguments, pipes, stderr, signals, and exit codes.

## Small syntax, explicit ambiguity rules

An object is the reserved `CLIPBOARD` source, an existing regular file, or literal text, in that order. An omitted object reads redirected stdin; an explicit object takes precedence. Quotes have only their normal shell meaning. Piping a filename stores the literal filename even when that file exists.

Those rules avoid separate commands for each content type. The cost is that a misspelled file path becomes text. This behavior is documented and tested. `--` handles option-looking literals, and `run` passes the command's remaining arguments through unchanged.

Bash completion queries the current store on every invocation. Its query is read-only, so pressing Tab does not create a store. `run` filters suggestions to executable entries.

**Evidence:** [parser and source tests](tests/test_register.py) exercise ambiguity and option placement; [completion tests](tests/test_completion.py) check new names, executable filtering, and an absent store.

## One clipboard interface, two adapters

[register_support.py](register_support.py) selects the desktop backend and negotiates an available content type. Clipboard capture prefers images, then text; output publishes stored bytes with their MIME type. Backend details stay out of the command interface.

Automated tests substitute desktop helpers to inspect byte and MIME handling without a graphical session. The [manual desktop checks](EXAMPLES.md#desktop-checks) cover the remaining boundary: whether a real application accepts a paste. A file-manager copy can represent a file reference rather than its image contents.

## Where to read the code

| Area | Implementation | Verification |
| --- | --- | --- |
| Parsing, storage, execution | [register](register) | [CLI tests](tests/test_register.py) |
| Detection and clipboard | [register_support.py](register_support.py) | [CLI and clipboard tests](tests/test_register.py) |
| Live Bash completion | [completion/register.bash](completion/register.bash) | [Completion tests](tests/test_completion.py) |
| Shell integration | [install.sh](install.sh), [uninstall.sh](uninstall.sh) | [Installer tests](tests/test_install.py) |
| Source exchange for maintainers | [MAINTAINING.md](MAINTAINING.md) | [Export](tests/test_export.py) and [update](tests/test_update.py) tests |

The [CI workflow](.github/workflows/tests.yml) exercises both distribution Python versions and a current Python. Parser behavior has differed between these versions, so using only a freshly installed interpreter would miss an important compatibility check.
