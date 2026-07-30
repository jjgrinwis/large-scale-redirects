# redirects-rs

A high-performance, large-scale HTTP redirect service implemented as a Spin application.
It uses a Finite State Transducer (FST) for efficient source path lookups and a Fast Compressed Static Dictionary (FCSD)
for compact storage of target URLs using Wizer.

**Based on**: This project is adapted from [akamai-developers/large-scale-redirects](https://github.com/akamai-developers/akamai-functions-samples/tree/main/samples/large-scale-redirects), ported to use Spin SDK 4.0 with the `#[http_component]` macro instead of the original Akamai EdgeWorkers implementation.

## Overview

- **Fast redirects**: O(1) lookup times with minimal memory footprint
- **Scalable**: Easily handles large numbers of redirects (millions)
- **Flexible**: Supports custom status codes for all or individual redirects
- **Validation**: Prevents redirect loops and invalid URLs
- **Optimization**: Detects and shortens redirect chains
- **Wasm-native**: Designed for WebAssembly runtimes with WASI HTTP support
- **Pre-initialized**: Embeds optimized representations of redirect data at build time for zero-cost cold starts

## 1. Managing Redirect Rules (`rules-manager`)

The `rules-manager` CLI validates, merges, and encodes redirect rules from plain text to optimized binary formats.

## Prerequisites

- [Spin](https://spinframework.dev/) - the CLI and runtime that builds and serves this application
- [Rust toolchain](https://www.rust-lang.org/tools/install) (stable version)
- The `wasm32-wasip1` target for Rust: `rustup target add wasm32-wasip1`
- [wizer](https://github.com/bytecodealliance/wizer): `cargo install wizer --all-features`

### Installing Spin

If you're new to the [Spin framework](https://spinframework.dev/), it's an open-source developer tool for building
and running serverless applications powered by WebAssembly. Install it with:

```shell
curl -fsSL https://spinframework.dev/downloads/install.sh | bash
```

See the [official installation guide](https://spinframework.dev/v3/install) for other install methods (Homebrew,
manual binary download, etc.) and the [quickstart guide](https://spinframework.dev/v3/quickstart) for a general
introduction to building Spin applications.

### Installing the remaining prerequisites

```shell
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env
rustup target add wasm32-wasip1
cargo install wizer --all-features
```

### Technology Choices

#### Why Spin SDK 4.0 and WASI P1 instead of SDK 6.0+ and P2/P3?

This project uses **Spin SDK 4.0** with the `#[http_component]` macro and **WASI Preview 1 (wasip1)** rather than the newer Spin SDK 6.0+ and WASI Preview 2/3 due to a critical dependency:

- **Wizer only supports the wasip1 module approach**: [Wizer](https://github.com/bytecodealliance/wizer) currently works exclusively with wasip1 modules and does not yet support the wasip2/wasip3 Component Model
- **Zero-cost cold starts require Wizer**: The entire architecture depends on pre-initializing the FST/FCSD data structures at build time using Wizer's snapshot capability
- **Spin SDK 6.0+ requires wasip2/wasip3**: The latest Spin SDK (v6.0.0, released July 2026) targets WASI 0.3.0 (wasip3) and the Component Model, which is incompatible with Wizer's current module-based approach

**The tradeoff:**

- With Wizer + wasip1: Instant first request, no initialization overhead, optimal cold start performance
- Without Wizer (using wasip2/3): Would need to load and deserialize FST/FCSD data on every cold start, adding latency

**Migration path:**
This project can upgrade to Spin SDK 6.0+ and wasip2/wasip3 once either:

1. Wizer adds support for the Component Model and wasip2/wasip3, OR
2. An alternative pre-initialization strategy becomes available for Component Model modules

Until then, wasip1 + Spin SDK 4.0 remains the correct choice for this use case.

#### What is Wizer and Why Use It?

[**Wizer**](https://github.com/bytecodealliance/wizer) is a WebAssembly snapshot tool that pre-initializes a Wasm module by running its initialization code ahead of time and capturing the resulting memory state.

**The core idea: move work from "every request" to "once, at build time".**

Without Wizer, every time a Wasm instance is spun up, it would have to open `sources.fst` and `targets.fcsd` from disk,
read their bytes, and parse them into the `fst::Map` and `fcsd::Set` structures before it could serve a single
request. With Wizer, that work happens exactly once, at build time on your machine, and the resulting in-memory
representation is baked directly into the Wasm binary.

**Why we use Wizer in this project:**

1. **Redirect data lives in memory, ready to query**: The FST and FCSD structures are fully loaded and deserialized
   into linear memory before the module is ever deployed. There is no file I/O or parsing on the request path — a
   lookup is a pure in-memory operation against data structures that are already built.
2. **Instant first request**: A freshly started Wasm instance already has the redirect data resident in memory, so
   even the very first request after a cold start is as fast as any other — no "loading" phase.
3. **Deterministic, O(1)-ish latency**: Because the data is pre-built and memory-resident, every request pays the
   same small, predictable cost to walk the FST — independent of how many millions of redirects are loaded.
4. **Reduced binary size**: The pre-initialized snapshot can be more compact than including raw data files and
   parsing code that would otherwise need to run at startup.

**How it works here:**

1. `src/lib.rs` declares three `OnceLock` statics — `SOURCES` (the FST), `TARGETS` (the FCSD), and
   `DEFAULT_STATUS_CODE` — which live for the lifetime of the module and hold the in-memory redirect data.
2. The `#[export_name = "wizer.initialize"]` function reads `sources.fst` and `targets.fcsd` from disk, parses them,
   and stores the resulting structures into those statics.
3. The `build.sh` script pipes the FST/FCSD file paths and default status code into Wizer, which runs that
   initialization function *once* and snapshots the resulting memory state directly into the output Wasm binary:

   ```bash
   echo "$1 $2 $3" | wizer --allow-wasi --wasm-bulk-memory true --dir . -o "$4" target/wasm32-wasip1/release/redirects_rs.wasm
   ```

4. At request time (in `handle()`), the HTTP handler simply calls `SOURCES.get()` and `TARGETS.get()` — reading data
   that is already sitting in memory, with no disk access and no re-parsing involved.

The net effect: all the expensive setup work (reading files, building the FST/FCSD structures) happens once on your
build machine, and every deployed instance starts up with a "hot" in-memory index ready to serve lookups immediately.

#### What are sources.fst and targets.fcsd?

These are the optimized binary data files that power fast redirect lookups:

- **sources.fst**: A [Finite State Transducer](https://github.com/BurntSushi/fst) file that maps source URL paths to target indices
  - Compresses common path prefixes (e.g., `/api/v1/...` stores `/api/v1/` once)
  - Provides O(n) lookup where n = path length, not number of redirects
  - Typically orders of magnitude smaller than a hash map
  - Generated by `rules-manager` from your validated redirect rules

- **targets.fcsd**: A [Fast Compressed Static Dictionary](https://github.com/BurntSushi/fst/tree/master/fst-bin) file that stores unique target URLs in compressed form
  - Deduplicates target URLs (many sources can redirect to the same target)
  - Uses dictionary compression to reduce memory footprint
  - Provides fast random access by index
  - Generated by `rules-manager` alongside the FST file

**The lookup flow:**

1. Request comes in for `/old/path`
2. Look up `/old/path` in `sources.fst` → get index `42`
3. Look up index `42` in `targets.fcsd` → get `/new/destination`
4. Return 302 redirect to `/new/destination`

This two-file architecture enables efficient storage of millions of redirects with minimal memory usage.

### Build the CLI

```shell
cargo build --release -p rules-manager
```

### Prepare Rule Files

Create text files with redirect rules in the format:

```
/old/path /new/path
/another/old/path https://example.com/destination
# Comments start with hash
/with-query /destination  # trailing comments work too
/with-custom /status-code 301 # Use custom status code instead of the default

# Blank lines are ignored
```

Rules must follow these conventions:

- Source paths must start with `/`
- Target can be a relative path (`/new/path`) or absolute URL (`https://example.com/path`)
- Each line must contain either two or three whitespace-separated parts, ignoring comments:
  - Two parts: source and target (default status code is used)
  - Three parts: source, target, and status code
- Source and target cannot be the same (would cause a self-loop)
- If provided, status codes must be valid
  [HTTP Redirection messages](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status#redirection_messages)

### Generating test rules

The `generate-rules.py` script can be used to generate test rules files adhering to the above requirements. It takes
some arguments (use `--help` for details) for configuring the number of rules and their properties, and prints the
result to stdout. Here's an example of using it to generate 100,000 rules:

```shell
python generate-rules.py -n 100000 > redirects.txt
```

To adjust the set of words used, edit the script itself.

### Run the CLI

```shell
./target/release/rules-manager \
  --add-rules example-redirects.txt \  # Optional: One or more new rule files
  --include-existing \                 # Optional: Include existing rules in output
  --output-dir ./output \              # Store all output files here (default: current directory)
  --rules-output-file redirects.txt \  # Where to store new validated rules (default: new_redirects.txt)
  --encoded-sources sources.fst \      # Binary FST output (default: sources.fst)
  --encoded-targets targets.fcsd \     # Binary FCSD output (default: targets.fcsd)
  --default-status-code 302            # Optional: Default status code for redirects
```

#### Validation Options

Control how the tool handles different validation issues:

```shell
./target/release/rules-manager \
  # ...other arguments...
  --self-loops warn \      # How to handle self-referential loops (ignore|warn|error)
  --loops error \          # How to handle multi-step loops (ignore|warn|error)
  --invalid-lines error    # How to handle malformed lines (ignore|warn|error)
```

### Validation Process

1. Loads and validates existing rules file (must have header: `# Validated redirects...`)
2. Processes new rule files and validates each rule
3. Checks for duplicate sources (newer rules override older ones)
4. Detects redirect loops (A→B→C→A) which would cause infinite redirects
5. Shortens redirect chains (e.g., A→B→C→D to A→D) as long as the entries have the same status code
6. Generates optimized binary files for fast lookups

### Example Workflow

Typical workflow for deploying redirects:

1. **Maintain a central validated rules file**:

   ```shell
   # First-time setup
   ./target/release/rules-manager --add-rules initial_rules.txt --rules-output-file validated_rules.txt

   # Later, add more rules or update existing ones, and store the result in a new file
   ./target/release/rules-manager --existing-rules validated_rules.txt --add-rules new_batch.txt --rules-output-file validated_rules_2.txt

   # Alternatively, update the existing rules file
   ./target/release/rules-manager --existing-rules validated_rules.txt --add-rules new_batch.txt --rules-output-file validated_rules.txt --include-existing
   ```

2. **Generate optimized files for production**:
   ```shell
   ./target/release/rules-manager --existing-rules validated_rules.txt \
     --encoded-sources sources.fst \
     --encoded-targets targets.fcsd
   ```

## 2. Building & Running the Wasm Component

### Prerequisites

Besides Spin, building the redirecter component requires the
[wizer](https://github.com/bytecodealliance/wizer) WebAssembly snapshot tool.

### Building

The Wasm component needs to be pre-initialized with the redirect data using the provided build script:

```shell
# Run the build script with paths to your FST and FCSD data files, the default status code, and the output path
# NOTE: The default status code provided here will be used for any rules missing an explicit status code,
#       regardless of the default status code used in the rules-manager.
./build.sh sources.fst targets.fcsd 302 target/redirect.wasm
```

The build process:

1. Compiles the Rust code to WebAssembly targeting wasip1
2. Uses Wizer to pre-initialize the Wasm module with your redirect data
3. Optionally optimizes the Wasm binary with wasm-opt if available
4. Outputs the final component to `target/redirect.wasm`

> **Note:** `spin.toml` already declares this same `build.sh` invocation under `[component.redirects-rs.build]`
> (using `sources.fst`/`targets.fcsd` in the project root). So instead of running `build.sh` yourself, you can let
> Spin drive the build via `spin build`, or combine build + run in one step with `spin up --build` (see below).

### Run with Spin

Using the included `spin.toml` file, you can build and run the redirect service locally with a single command:

```shell
spin up --build
```

This runs the `build.sh` script defined in `spin.toml` (compiling the Wasm component and pre-initializing it with
Wizer) and then starts the Spin runtime, listening on `http://localhost:3000` by default. If you've already built the
component and just want to (re)start the server, you can drop the `--build` flag:

```shell
spin up
```

Test redirects:

```shell
# Should return 302 Found with Location header
curl -I http://localhost:3000/old/path

# Should return 404 Not Found
curl -I http://localhost:3000/nonexistent
```

### Trying it Live on Akamai Functions

If you'd like to try this running live on the Akamai Functions platform, request preview access:
[https://fibsu0jcu2g.typeform.com/fwf-preview?typeform-source=developer.fermyon.com](https://fibsu0jcu2g.typeform.com/fwf-preview?typeform-source=developer.fermyon.com)

Once your access is active:

```shell
# Install the Spin Akamai plugin
spin plugins install aka

# Log in to Akamai Functions
spin aka login

# Deploy the Wasm component to the global Akamai Functions platform
spin aka deploy
```

See the [Akamai Functions documentation](https://techdocs.akamai.com/akamai-functions/docs/welcome) for more details.

## 3. Architecture

### Data Structures

- **Finite State Transducer (FST)**: Maps source paths to target indices with minimal memory overhead
  - Perfect for URL paths - compresses common prefixes
  - O(n) where n is the length of the lookup key (not the number of redirects)
  - Provides ordered iteration and prefix searching capabilities

- **Fast Compressed Static Dictionary (FCSD)**: Stores unique target URLs in compressed format
  - Significantly reduces memory usage compared to storing URLs directly
  - Provides fast decoding using a pre-computed lookup table

### Component Design

- **rules-manager (Rust CLI)**
  - Handles rule parsing, validation, and encoding
  - Produces human-readable validated rules and optimized binary files

- **redirects-rs (Wasm Component)**
  - Built with Spin SDK 4.0 using the `#[http_component]` macro
  - Pre-initialized static data structures via `wizer.initialize`
  - Implements `wasi:http/incoming-handler` interface
  - Keeps memory usage constant regardless of request volume
  - Process:
    1. Extract URL path from incoming request
    2. Look up path in FST to get target index
    3. Use index to retrieve target URL from FCSD
    4. Check for and potentially extract custom status code or use default
    5. Return HTTP redirect with the selected status code and Location header set to the rule's target URL (or 404 if
       not found)
