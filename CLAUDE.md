# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A high-performance HTTP redirect service built as a Spin/WebAssembly component. It looks up incoming
request paths against a pre-built FST (Finite State Transducer) to get a target index, then resolves
that index against an FCSD (Fast Compressed Static Dictionary) to get the destination URL. Both data
structures are generated ahead of time by the `rules-manager` CLI from a plain-text rules file, and are
baked directly into the Wasm binary's memory at build time via Wizer (see "Wizer / build pipeline" below).

Two Cargo crates:
- **`redirects-rs`** (root `src/lib.rs`) — the Spin HTTP component that serves redirects.
- **`rules-manager`** (`rules-manager/src/main.rs`) — CLI that validates/merges redirect rules and encodes
  the `sources.fst` / `targets.fcsd` binary files consumed by `redirects-rs`.

This is a Rust port of Akamai's [large-scale-redirects sample](https://github.com/akamai-developers/akamai-functions-samples/tree/main/samples/large-scale-redirects),
adapted to Spin SDK 4.0 with `#[http_component]`.

## Commands

### Build & run the redirect service
```shell
spin up --build          # builds (via build.sh) and runs on http://localhost:3000
spin up                  # run without rebuilding
spin build                # just build, driven by [component.redirects-rs.build] in spin.toml
```

Manual build (what `spin build` invokes under the hood):
```shell
./build.sh sources.fst targets.fcsd 302 redirects.wasm
```
This compiles to `wasm32-wasip1`, runs Wizer to pre-initialize the module with the FST/FCSD data (piped
in as stdin args: `sources_path targets_path default_status_code`), and optimizes with `wasm-opt` if available.

Prereqs: `rustup target add wasm32-wasip1`, `cargo install wizer --all-features`, the `spin` CLI.

### rules-manager CLI
```shell
cargo build --release -p rules-manager
cargo test -p rules-manager             # run all rules-manager tests
cargo test -p rules-manager test_name   # run a single test
```

Typical usage — merge new rules into a validated rules file and emit encoded binaries:
```shell
./target/release/rules-manager \
  --existing-rules validated_rules.txt \
  --add-rules new_batch.txt \
  --rules-output-file validated_rules.txt --include-existing \
  --encoded-sources sources.fst --encoded-targets targets.fcsd \
  --default-status-code 302
```
Key flags: `--self-loops`/`--loops`/`--invalid-lines` each take `ignore|warn|error` to control validation
strictness. Existing-rules files must start with the header `# Validated redirects...` (rules-manager
writes this itself; hand-edited files without it are rejected).

### Generating synthetic test rules
```shell
python generate-rules.py -n 100000 > redirects.txt
```

## Architecture

### Request path (`src/lib.rs`)
1. `#[http_component] fn handle` extracts `path_and_query()` from the request.
2. Looks it up in the `SOURCES` FST (`fst::Map<Vec<u8>>`) to get a `u64` target index.
3. Decodes that index via `TARGETS` (`fcsd::Set`) to get the raw target bytes.
4. If the decoded bytes end in a space + 3-digit status code (e.g. `"/dest 301"`), that suffix is parsed
   out as a per-rule status code override; otherwise the global `DEFAULT_STATUS_CODE` is used.
5. Returns a redirect response with `Location` header, or 404 if the path isn't found.

### Wizer / build pipeline
`SOURCES`, `TARGETS`, and `DEFAULT_STATUS_CODE` are `OnceLock` statics populated once by the
`#[export_name = "wizer.initialize"]` function, which reads `sources.fst` / `targets.fcsd` from disk and
parses the args (source path, target path, default status code) from stdin. `build.sh` runs Wizer against
the compiled wasip1 module, snapshotting that already-initialized memory state directly into the shipped
Wasm binary — so deployed instances never touch disk or parse anything at request time; the data is
already resident in memory on cold start.

**Why wasip1 + Spin SDK 4.0 instead of newer SDK/WASI versions**: Wizer only supports wasip1 modules, not
the wasip2/wasip3 Component Model used by Spin SDK 6.0+. This project is intentionally pinned to SDK 4.0 +
`#[http_component]` until Wizer (or an equivalent pre-initialization mechanism) supports the Component
Model — do not "upgrade" the SDK dependency without addressing this.

### rules-manager (`rules-manager/src/main.rs`)
Rule file format: `<source> <target> [status_code]`, `#` starts a comment (inline or full-line), source
must start with `/`, target may be a relative path or absolute `http(s)` URL.

Processing pipeline in `run()` / `RedirectsMap::build`:
1. Parse existing (already-validated) rules and new rule files into a `HashMap<source, MapEntry>`.
2. Validate each line (`is_valid_redirect_source`/`is_valid_redirect_target`, status code range 301-399,
   source != target) per the configured `ValidationBehavior` (ignore/warn/error).
3. `check_for_loops` walks each chain from every source and errors if it revisits a node (detects
   multi-hop redirect loops, not just self-loops).
4. `shorten_chains` collapses A→B→C→D into A→D wherever consecutive hops share the same status code
   (chains with differing status codes are preserved unshortened at each hop).
5. Writes the merged, validated rule set back out (optionally excluding rules already present in the
   existing file), then encodes:
   - `sources.fst`: keys are sorted source paths, values are indices into the sorted+deduped target list.
   - `targets.fcsd`: the sorted, deduplicated target strings (non-default status codes appended as
     `"<target> <code>"` before dedup/encoding).

Test suite in `rules-manager/src/main.rs` (bottom `#[cfg(test)] mod tests`) covers loop detection (single
file, cross-file, self-loop), chain shortening with/without status codes, validation-behavior handling,
and full `run()` round-trips writing/reading files via `tempfile`.
