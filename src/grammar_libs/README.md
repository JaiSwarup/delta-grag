# Grammar Libraries Directory

This directory is reserved for prebuilt Tree-sitter grammar shared libraries that may be bundled into container images for fully offline runtime environments.

## Purpose

Some deployment targets require all parser assets to be available inside the image at build time, without fetching anything from the internet when the container starts. This directory provides a stable location for those artifacts if and when they are added.

Typical examples include:

- `tree-sitter-python.so`
- `tree-sitter-python.dll`
- `tree-sitter-python.dylib`

## Current status

At the moment, this project primarily relies on Python package-based Tree-sitter support rather than custom repository-bundled grammar binaries.

That means:

- this directory may be empty or contain only documentation
- runtime parsing support is currently resolved through installed Python dependencies
- no custom grammar compilation step is required by the repository itself right now

## Why keep this directory

The Docker and deployment setup references a dedicated grammar location so the project can be extended later without changing container layout assumptions.

If offline grammar artifacts are introduced later, they should be placed here and copied into the runtime image during the Docker build.

## Recommended conventions

If you add grammar binaries here later:

1. Name files clearly by language and platform.
2. Document how they were produced.
3. Keep versions aligned with the runtime `tree-sitter` dependency.
4. Avoid downloading or compiling them at container startup.
5. Update `docs/docker_setup.md` if the container build process changes.

## Example future layout

```text
src/grammar_libs/
  README.md
  tree-sitter-python.so
  tree-sitter-python.dll
  tree-sitter-python.dylib
```

## Maintenance note

Do not add placeholder binary files just to satisfy directory expectations. Only commit real runtime artifacts that are actually used and documented.