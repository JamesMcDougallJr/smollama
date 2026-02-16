# PRD: Plugin System Architecture

## Introduction

Create a formal plugin system to enable hardware-specific sensors and tools without bloating the core smollama application. Different Raspberry Pi models (and other hardware) have varying compiled libraries and Python packages. The plugin architecture isolates these dependencies, keeping the core application (dashboard + Ollama client) minimal while supporting diverse hardware configurations.

## Goals

- Enable third-party developers to add custom sensors and tools through a clean plugin interface
- Isolate hardware-specific dependencies (GPIO libraries, I2C, etc.) from core application
- Support multiple Raspberry Pi models and other hardware without conditional imports in core
- Provide plugin discovery, lifecycle management, and validation
- Move existing GPIO and System providers to `plugins/builtin/` directory
- Add CLI tooling for plugin installation and management
- Maintain backward compatibility for existing configurations

## User Stories

### US-001: Define SensorPlugin interface
**Description:** As a plugin developer, I need a clear interface for creating sensor plugins so I can add custom hardware support.

**Acceptance Criteria:**
- [ ] Create `smollama/plugins/base.py` with `SensorPlugin` abstract class
- [ ] Interface extends existing `ReadingProvider` from `smollama/readings/base.py`
- [ ] Add lifecycle hooks: `setup()`, `teardown()`
- [ ] Add `config_schema` property for plugin-specific config validation (JSON Schema)
- [ ] Add `metadata` property returning `PluginMetadata` (name, version, author, description, dependencies)
- [ ] Add `check_dependencies()` method that returns bool and optional error message
- [ ] Typecheck passes

### US-002: Define ToolPlugin interface
**Description:** As a plugin developer, I need a clear interface for creating tool plugins so custom tools can be added without modifying core code.

**Acceptance Criteria:**
- [ ] Create `ToolPlugin` abstract class in `smollama/plugins/base.py`
- [ ] Interface extends existing `Tool` from `smollama/tools/base.py`
- [ ] Add same lifecycle hooks as SensorPlugin: `setup()`, `teardown()`, `config_schema`, `metadata`, `check_dependencies()`
- [ ] Support for multiple tools per plugin (plugin can register several related tools)
- [ ] Typecheck passes

### US-003: Create plugin loader and discovery
**Description:** As a system operator, I want plugins to be automatically discovered from the plugins directory so I don't need to manually register each one.

**Acceptance Criteria:**
- [ ] Create `smollama/plugins/loader.py` with `PluginLoader` class
- [ ] Scans `smollama/plugins/` directory for Python modules
- [ ] Discovers all `SensorPlugin` and `ToolPlugin` subclasses via inspection
- [ ] Supports additional plugin paths from config: `plugins.paths: ["./my_plugins"]`
- [ ] Validates plugin metadata on discovery (name, version required)
- [ ] Returns list of discovered plugins with metadata
- [ ] Typecheck passes

### US-004: Implement plugin dependency checking
**Description:** As a system operator, I want plugins with missing dependencies to be skipped gracefully so the application starts even if some hardware isn't available.

**Acceptance Criteria:**
- [ ] `PluginLoader.load_plugin()` calls `plugin.check_dependencies()` before initialization
- [ ] Plugins with unmet dependencies are skipped silently
- [ ] Log debug message for each skipped plugin: "Skipping plugin {name}: {reason}"
- [ ] Skipped plugins are tracked in loader state for status reporting
- [ ] Typecheck passes

### US-005: Add plugin lifecycle management
**Description:** As the core application, I need to properly initialize and cleanup plugins so resources are managed correctly.

**Acceptance Criteria:**
- [ ] `PluginLoader.initialize_plugins()` calls `setup()` on each loaded plugin
- [ ] `PluginLoader.shutdown_plugins()` calls `teardown()` on each plugin
- [ ] Exceptions in `setup()` are caught, logged, and plugin is marked as failed
- [ ] `teardown()` is always called for successfully initialized plugins, even if others fail
- [ ] Application shutdown hook registered to call `shutdown_plugins()`
- [ ] Typecheck passes

### US-006: Move GPIOReadingProvider to builtin plugin
**Description:** As a developer, I want the existing GPIO provider to become a plugin so hardware-specific code is isolated from core.

**Acceptance Criteria:**
- [ ] Create `smollama/plugins/builtin/gpio_plugin.py`
- [ ] Wrap existing `GPIOReadingProvider` logic in new `GPIOSensorPlugin` class
- [ ] Implement `SensorPlugin` interface (setup, teardown, config_schema, metadata)
- [ ] `check_dependencies()` verifies `gpiozero` is available
- [ ] Metadata declares dependency on `gpiozero`, `RPi.GPIO`
- [ ] Plugin loads successfully on Raspberry Pi hardware
- [ ] Plugin is skipped gracefully on non-Pi hardware
- [ ] Typecheck passes

### US-007: Move SystemReadingProvider to builtin plugin
**Description:** As a developer, I want the system stats provider to become a plugin for consistency, even though it has no hardware dependencies.

**Acceptance Criteria:**
- [ ] Create `smollama/plugins/builtin/system_plugin.py`
- [ ] Wrap existing `SystemReadingProvider` logic in new `SystemSensorPlugin` class
- [ ] Implement `SensorPlugin` interface
- [ ] `check_dependencies()` verifies `psutil` is available (should always pass)
- [ ] Metadata declares dependency on `psutil`
- [ ] Plugin loads successfully on all platforms
- [ ] Typecheck passes

### US-008: Update ReadingManager to use plugin system
**Description:** As the reading manager, I need to integrate with the plugin loader so sensor plugins are automatically registered.

**Acceptance Criteria:**
- [ ] Modify `smollama/readings/manager.py` to use `PluginLoader`
- [ ] Load sensor plugins on initialization instead of hard-coded providers
- [ ] Each loaded `SensorPlugin` is registered as a `ReadingProvider`
- [ ] Maintain backward compatibility: existing config still works
- [ ] Typecheck passes

### US-009: Add plugin configuration schema validation
**Description:** As a plugin developer, I want my plugin's config to be validated so users get clear error messages for misconfigurations.

**Acceptance Criteria:**
- [ ] Create `smollama/plugins/config.py` with config validation logic
- [ ] Use JSON Schema to validate plugin-specific config sections
- [ ] Validation happens during plugin loading (before `setup()`)
- [ ] Invalid config logs clear error message with schema expectations
- [ ] Plugins with invalid config are not loaded
- [ ] Typecheck passes

### US-010: Update config.yaml structure for plugins
**Description:** As a system operator, I want to configure plugins in a clear, structured way in config.yaml.

**Acceptance Criteria:**
- [ ] Add `plugins` section to `config.example.yaml`
- [ ] Structure supports `paths`, `builtin`, and `custom` subsections
- [ ] Example shows GPIO and System builtin plugins with `enabled: true`
- [ ] Example shows custom plugin with name, module, and config
- [ ] Update `smollama/config.py` to parse new structure
- [ ] Document new config format in README or docs
- [ ] Typecheck passes

### US-011: Add plugin CLI management commands
**Description:** As a system operator, I want to install plugins from remote sources without manually copying files.

**Acceptance Criteria:**
- [ ] Add `smollama plugin install <source>` command to CLI
- [ ] Support installation from git URLs: `smollama plugin install https://github.com/user/plugin.git`
- [ ] Support installation from local paths: `smollama plugin install ./my_plugin`
- [ ] Plugin installed to `~/.smollama/plugins/` or configurable plugins directory
- [ ] Command validates plugin after installation (loads and checks dependencies)
- [ ] Clear success/failure messages
- [ ] Typecheck passes

### US-012: Add plugin list command
**Description:** As a system operator, I want to see which plugins are installed and their status.

**Acceptance Criteria:**
- [ ] Add `smollama plugin list` command to CLI
- [ ] Shows all discovered plugins (builtin and custom)
- [ ] Status for each: enabled, disabled, failed (with reason), missing dependencies
- [ ] Shows plugin metadata: name, version, author, description
- [ ] Color-coded output (green=loaded, yellow=disabled, red=failed)
- [ ] Typecheck passes

### US-013: Create plugin development documentation
**Description:** As a plugin developer, I need clear documentation on how to create and distribute plugins.

**Acceptance Criteria:**
- [ ] Create `docs/plugin-development.md` or update README
- [ ] Document `SensorPlugin` interface with example
- [ ] Document `ToolPlugin` interface with example
- [ ] Explain plugin discovery mechanism
- [ ] Explain dependency management (per-plugin requirements)
- [ ] Provide minimal working example plugin
- [ ] Document config schema validation
- [ ] Document plugin installation process

### US-014: Create example I2C sensor plugin
**Description:** As a reference implementation, create an example plugin for I2C temperature sensors to demonstrate the pattern.

**Acceptance Criteria:**
- [ ] Create `examples/plugins/i2c_temp_plugin.py`
- [ ] Implements `SensorPlugin` interface
- [ ] Supports BME280 or DHT22 sensor (simulated if hardware not available)
- [ ] Demonstrates config schema validation (bus, address parameters)
- [ ] `check_dependencies()` verifies I2C libraries are available
- [ ] Includes inline documentation and comments
- [ ] Typecheck passes

## Functional Requirements

- FR-1: Plugin system MUST support both `SensorPlugin` and `ToolPlugin` interfaces
- FR-2: Plugins MUST declare dependencies in metadata
- FR-3: Plugin loader MUST skip plugins with unmet dependencies gracefully (log debug message)
- FR-4: Plugin loader MUST validate plugin config against declared JSON Schema before loading
- FR-5: Plugins MUST implement lifecycle hooks: `setup()` and `teardown()`
- FR-6: Each plugin MUST provide metadata: name, version, author, description, dependencies
- FR-7: Plugin loader MUST scan `smollama/plugins/` directory automatically
- FR-8: Plugin loader MUST support additional paths from config: `plugins.paths`
- FR-9: Builtin plugins (GPIO, System) MUST be moved to `plugins/builtin/` directory
- FR-10: Application MUST maintain backward compatibility with existing config.yaml
- FR-11: CLI MUST provide `plugin install <source>` command supporting git URLs and local paths
- FR-12: CLI MUST provide `plugin list` command showing all plugins and their status
- FR-13: Core smollama MUST NOT import hardware-specific libraries (gpiozero, RPi.GPIO, etc.)
- FR-14: Plugin dependencies MUST be isolated (each plugin can have separate requirements)
- FR-15: Failed plugins MUST NOT prevent application startup

## Non-Goals (Out of Scope)

- Plugin marketplace or centralized registry (deferred to future)
- Plugin versioning and dependency resolution (use manual version pinning)
- Plugin hot-reloading (requires application restart)
- Plugin sandboxing or security isolation (plugins are trusted code)
- Plugin inter-dependencies (plugins should be independent)
- Web UI for plugin management (CLI only for now)
- Binary/compiled plugins (Python only)
- Plugin signing or verification (trust model)

## Design Considerations

### Plugin Directory Structure
```
smollama/
├── plugins/
│   ├── __init__.py
│   ├── base.py              # SensorPlugin, ToolPlugin interfaces
│   ├── loader.py            # PluginLoader class
│   ├── config.py            # Config validation
│   └── builtin/
│       ├── __init__.py
│       ├── gpio_plugin.py   # GPIOSensorPlugin
│       └── system_plugin.py # SystemSensorPlugin
└── ...

~/.smollama/plugins/         # User-installed plugins
└── my_custom_plugin/
    ├── __init__.py
    ├── plugin.py
    └── requirements.txt
```

### Plugin Metadata Structure
```python
@dataclass
class PluginMetadata:
    name: str
    version: str
    author: str
    description: str
    dependencies: list[str]  # e.g., ["gpiozero>=2.0", "RPi.GPIO"]
    plugin_type: str         # "sensor" | "tool"
```

### Config Structure
```yaml
plugins:
  paths: ["./my_plugins", "~/.smollama/plugins"]
  builtin:
    gpio:
      enabled: true
    system:
      enabled: true
  custom:
    - name: bme280
      module: i2c_temp_plugin
      enabled: true
      config:
        bus: 1
        address: 0x76
```

### Dependency Isolation Strategy
- Each plugin directory can have its own `requirements.txt`
- Plugin installation command: `pip install -r <plugin_dir>/requirements.txt`
- Core smollama has minimal dependencies (FastAPI, Ollama client, SQLite)
- Hardware-specific dependencies only loaded when plugin is active

## Technical Considerations

### Backward Compatibility
- Existing `config.yaml` files continue to work
- If no `plugins` section exists, auto-enable builtin plugins with default config
- Migrate existing GPIO/System config to new plugin config format automatically

### Error Handling
- Exceptions in plugin `setup()` are caught and logged, plugin marked as failed
- Missing dependencies cause silent skip (debug log only)
- Invalid config causes plugin to not load (error log with details)
- Application continues to run even if all plugins fail

### Performance
- Plugin discovery happens once on startup (not on every request)
- Plugin metadata cached in memory
- No performance impact for core dashboard/agent functionality

### Testing Strategy
- Unit tests for `PluginLoader` (mocked plugins)
- Integration tests for builtin plugins on Pi hardware
- Test graceful degradation when dependencies missing
- Test config validation with valid/invalid schemas

## Success Metrics

- Core smollama has zero hardware-specific imports after migration
- Builtin plugins (GPIO, System) work identically to before refactor
- Plugin with missing dependencies skips gracefully without error
- Custom plugin can be installed and loaded in under 5 minutes
- Plugin developer can create new sensor plugin following docs in under 30 minutes

## Open Questions

1. **Should plugins be able to define their own MQTT topics?**
   - Probably yes - plugin config can include MQTT topic patterns
   - Deferred to plugin implementation, not core framework

2. **How should plugin conflicts be handled?** (e.g., two plugins register same source_id)
   - Fail-fast on conflict detection (last one wins with warning log?)
   - Decision: Fail-fast, log error, second plugin not loaded

3. **Should there be a plugin "priority" or load order?**
   - Probably not needed initially
   - Plugins should be independent
   - Decision: Load in alphabetical order for consistency

4. **How to handle plugin upgrades?**
   - Manual: `smollama plugin uninstall <name>` then `smollama plugin install <source>`
   - Or: `smollama plugin upgrade <name>` that re-installs from original source
   - Decision: Start with manual, add upgrade command later if needed

5. **Should builtin plugins be optional in pyproject.toml?**
   - e.g., `pip install smollama[gpio]` installs GPIO dependencies
   - Decision: Yes, use optional dependencies for builtin plugins
   - Core: `pip install smollama` (no hardware deps)
   - GPIO: `pip install smollama[gpio]` (adds gpiozero, RPi.GPIO)
   - System: included in core (psutil is lightweight)
