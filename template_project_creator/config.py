"""Configuration loading and inexpensive, offline validation."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ConfigurationError(ValueError):
    """The template configuration is incomplete or internally inconsistent."""


def load_config(path: str | Path) -> tuple[dict[str, Any], Path]:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise ConfigurationError(f"Configuration file does not exist: {path}")
    try:
        if path.suffix.lower() in {".yaml", ".yml"}:
            try:
                import yaml
            except ImportError as exc:
                raise ConfigurationError("YAML configuration requires PyYAML") from exc
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"Cannot parse {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("Top-level configuration must be an object")
    validate_config(value)
    return value, path.parent


def _named(items: Any, label: str) -> set[str]:
    if not isinstance(items, list):
        raise ConfigurationError(f"{label} must be a list")
    names = set()
    for item in items:
        if not isinstance(item, dict) or not item.get("name"):
            raise ConfigurationError(f"Every {label} item needs a non-empty name")
        if item["name"] in names:
            raise ConfigurationError(f"Duplicate {label} name: {item['name']}")
        names.add(item["name"])
    return names


def validate_config(config: dict[str, Any]) -> None:
    if config.get("version") != 1:
        raise ConfigurationError("Configuration 'version' must be 1")
    for key in ("connections", "zones", "datasets", "folders", "recipes"):
        config.setdefault(key, [])
    connection_names = _named(config["connections"], "connections")
    zone_names = _named(config["zones"], "zones")
    dataset_names = _named(config["datasets"], "datasets")
    folder_names = _named(config["folders"], "folders")
    recipe_names = _named(config["recipes"], "recipes")
    invalid_recipe_names = sorted(name for name in recipe_names if "." in name)
    if invalid_recipe_names:
        raise ConfigurationError(
            "Recipe names cannot contain '.': " + ", ".join(invalid_recipe_names)
        )
    if "project" in config and not isinstance(config["project"], dict):
        raise ConfigurationError("project must be an object")
    for item in config["datasets"] + config["folders"]:
        if item.get("connection") and item["connection"] not in connection_names:
            # An externally provisioned connection is explicitly allowed.
            if not item.get("external_connection", False):
                raise ConfigurationError(
                    f"{item['name']} references connection {item['connection']!r}, "
                    "which is not declared (set external_connection: true if pre-existing)"
                )
        if item.get("zone") and item["zone"] not in zone_names:
            raise ConfigurationError(f"{item['name']} references unknown zone {item['zone']!r}")
    sample_names: set[str] = set()
    for sample in config.get("sample_data", []):
        if not isinstance(sample, dict) or not sample.get("name"):
            raise ConfigurationError("Every sample_data item needs a non-empty name")
        if sample["name"] in sample_names:
            raise ConfigurationError(f"Duplicate sample_data name: {sample['name']}")
        sample_names.add(sample["name"])
    for dataset in config["datasets"]:
        if dataset.get("generated_sample") and dataset["generated_sample"] not in sample_names:
            raise ConfigurationError(
                f"Dataset {dataset['name']} references unknown sample_data {dataset['generated_sample']!r}"
            )
    known_objects = dataset_names | folder_names
    for recipe in config["recipes"]:
        if recipe.get("zone") and recipe["zone"] not in zone_names:
            raise ConfigurationError(f"{recipe['name']} references unknown zone {recipe['zone']!r}")
        for ref in recipe.get("references", []):
            # Cross-project references are legal and deliberately not validated locally.
            if "." not in ref and ref not in known_objects:
                raise ConfigurationError(f"Recipe {recipe['name']} references unknown object {ref!r}")
        definition = recipe.get("definition", {})
        for direction in ("inputs", "outputs"):
            roles = definition.get(direction, {})
            if not isinstance(roles, dict):
                raise ConfigurationError(f"Recipe {recipe['name']} definition.{direction} must be an object")
            for role, link in roles.items():
                items = link.get("items") if isinstance(link, dict) else None
                if not isinstance(items, list):
                    raise ConfigurationError(
                        f"Recipe {recipe['name']} definition.{direction}.{role}.items must be a list"
                    )
                for item in items:
                    ref = item.get("ref") if isinstance(item, dict) else None
                    if not isinstance(ref, str) or not ref:
                        raise ConfigurationError(
                            f"Recipe {recipe['name']} has an invalid {direction} link in role {role!r}"
                        )
                    if "." not in ref and ref not in known_objects:
                        raise ConfigurationError(
                            f"Recipe {recipe['name']} {direction} unknown object {ref!r}"
                        )
        if recipe.get("code_template") and recipe.get("type") not in {"python", "sql_query", "hive", "impala"}:
            raise ConfigurationError(f"Recipe {recipe['name']} uses code_template but is not a code recipe")
    for model in config.get("models", []):
        if not isinstance(model, dict) or not model.get("folder"):
            raise ConfigurationError("Every models item needs a folder")
        if model["folder"] not in folder_names:
            raise ConfigurationError(f"Model references unknown folder {model['folder']!r}")
        if model.get("sample_data") not in sample_names:
            raise ConfigurationError(f"Model references unknown sample_data {model.get('sample_data')!r}")
