# UV Migration

Migrate from pip to UV for faster dependency installation and resolution. UV is a Rust-based Python package manager by Astral (makers of Ruff) that is 10-100x faster than pip.

## Why UV?

- **Speed**: 10-100x faster than pip for dependency resolution and installation
- **Deterministic**: Built-in lockfile support for reproducible builds
- **Developer Experience**: Simpler commands (`uv sync` vs `pip install -e ".[dev]"`)
- **Modern**: First-class support for PEP 621 (pyproject.toml)
- **Edge-Friendly**: Faster installs especially valuable on Raspberry Pi

## Migration Tasks

### 1. Verify UV compatibility
- **Status**: Not started
- **Effort**: Trivial
- Test current `pyproject.toml` with `uv sync`
- Verify all dependency groups resolve correctly
- No file changes required (verification only)

### 2. Generate lockfile
- **Status**: Not started
- **Effort**: Trivial
- Run `uv lock` to create `uv.lock`
- Commit lockfile to git for reproducible builds
- **Files**: `uv.lock` (new)

### 3. Update installation documentation
- **Status**: Not started
- **Effort**: Small
- Replace pip commands with UV equivalents in README.md
- Document both UV-native and UV-pip compatibility modes
- **Files**: `README.md` (Installation section)
- **Changes**:
  - `pip install -e .` → `uv sync` or `uv pip install -e .`
  - `pip install -e ".[all]"` → `uv sync --all-extras`
  - `pip install -e ".[dev]"` → `uv sync --extra dev`

### 4. Document developer workflow
- **Status**: Not started
- **Effort**: Small
- Create development workflow section using UV
- **Files**: `README.md` or new `CONTRIBUTING.md`
- **Commands to document**:
  - `uv sync` - Install all dependencies
  - `uv add <package>` - Add new dependency
  - `uv add --dev <package>` - Add dev dependency
  - `uv lock --upgrade` - Update lockfile
  - `uv run pytest` - Run tests in UV environment

### 5. Update install scripts (future)
- **Status**: Not started (blocked until scripts exist)
- **Effort**: Small
- Modify `scripts/install.sh` to install UV first, then use UV commands
- Modify `scripts/setup-pi.sh` to use UV for faster Pi installs
- **Files**: `scripts/install.sh`, `scripts/setup-pi.sh` (planned, not yet created)
- Note: This task becomes relevant when install scripts are implemented

### 6. Optional: Python version pinning
- **Status**: Not started
- **Effort**: Trivial
- Create `.python-version` with minimum version (e.g., `3.10`)
- Enables UV's automatic Python version management
- **Files**: `.python-version` (new)

### 7. Optional: Configure venv management
- **Status**: Not started
- **Effort**: Trivial
- Update `.gitignore` to include `.venv/` if using UV's venv
- Document venv strategy (UV-managed vs external)
- **Files**: `.gitignore`

## Benefits

- Faster CI/CD pipelines (when implemented)
- Quicker development setup for new contributors
- Reproducible builds via lockfile
- Better edge device experience (Raspberry Pi installations)
- Modern dependency management tooling
