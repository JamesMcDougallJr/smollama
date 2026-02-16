"""Plugin configuration validation using JSON Schema."""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def validate_plugin_config(
    plugin_name: str, config: dict[str, Any], schema: dict[str, Any]
) -> tuple[bool, str | None]:
    """Validate plugin configuration against JSON Schema.

    Args:
        plugin_name: Name of the plugin (for error messages).
        config: The configuration dict to validate.
        schema: JSON Schema dict describing expected structure.

    Returns:
        Tuple of (valid, error_message).
        - (True, None) if config is valid
        - (False, "error details") if config is invalid
    """
    try:
        import jsonschema  # type: ignore[import-untyped]
        from jsonschema import Draft7Validator
    except ImportError:
        logger.warning(
            "jsonschema not installed, skipping config validation. "
            "Install with: pip install jsonschema"
        )
        return (True, None)  # Skip validation if jsonschema not available

    try:
        validator = Draft7Validator(schema)
        errors = list(validator.iter_errors(config))

        if not errors:
            return (True, None)

        # Format errors into a readable message
        error_msgs = []
        for error in errors:
            path = ".".join(str(p) for p in error.path) if error.path else "root"
            error_msgs.append(f"  - {path}: {error.message}")

        error_text = (
            f"Invalid configuration for plugin '{plugin_name}':\n"
            + "\n".join(error_msgs)
        )
        return (False, error_text)

    except jsonschema.SchemaError as e:
        # Schema itself is invalid
        error_msg = (
            f"Invalid JSON Schema for plugin '{plugin_name}': {e.message}"
        )
        logger.error(error_msg)
        return (False, error_msg)

    except Exception as e:
        error_msg = (
            f"Unexpected error validating config for plugin '{plugin_name}': {e}"
        )
        logger.error(error_msg, exc_info=True)
        return (False, error_msg)


def get_empty_config_schema() -> dict[str, Any]:
    """Get a permissive schema that accepts any config.

    Use this as a default for plugins that don't need config validation.

    Returns:
        JSON Schema that accepts any object.
    """
    return {
        "type": "object",
        "additionalProperties": True,
    }
