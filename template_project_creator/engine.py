"""Orchestration, dependency checks, zone restoration, and validation."""
from __future__ import annotations

import logging
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from .config import ConfigurationError
from .creators import (ConnectionCreator, DatasetCreator, FolderCreator, ProjectCreator,
                       RecipeCreator, ZoneCreator)
from .model_generator import ModelGenerator
from .sample_data_generator import SampleDataGenerator

LOG = logging.getLogger(__name__)


class DependencyResolver:
    """Checks declarative recipe links before changing DSS."""
    def __init__(self, config: dict[str, Any]): self.config = config

    def resolve_recipes(self) -> list[dict[str, Any]]:
        objects = {x["name"] for x in self.config["datasets"] + self.config["folders"]}
        producers: dict[str, str] = {}
        recipe_by_name = {recipe["name"]: recipe for recipe in self.config["recipes"]}
        dependencies: dict[str, set[str]] = defaultdict(set)
        for recipe in self.config["recipes"]:
            definition = recipe.get("definition", recipe.get("proto", {}))
            for role in definition.get("outputs", {}).values():
                for item in role.get("items", []):
                    ref = item.get("ref", "")
                    if "." not in ref and ref not in objects:
                        raise ConfigurationError(f"Recipe {recipe['name']} outputs unknown object {ref!r}")
                    if "." not in ref and ref in producers:
                        raise ConfigurationError(f"Object {ref!r} has two producers: {producers[ref]}, {recipe['name']}")
                    if "." not in ref: producers[ref] = recipe["name"]
        for recipe in self.config["recipes"]:
            definition = recipe.get("definition", recipe.get("proto", {}))
            for role in definition.get("inputs", {}).values():
                for item in role.get("items", []):
                    ref = item.get("ref", "")
                    if "." not in ref and ref not in objects:
                        raise ConfigurationError(f"Recipe {recipe['name']} inputs unknown object {ref!r}")
                    producer = producers.get(ref)
                    if producer and producer != recipe["name"]:
                        dependencies[recipe["name"]].add(producer)
        ordered, ready = [], sorted(name for name in recipe_by_name if not dependencies[name])
        while ready:
            name = ready.pop(0)
            ordered.append(recipe_by_name[name])
            for candidate in sorted(recipe_by_name):
                if name in dependencies[candidate]:
                    dependencies[candidate].remove(name)
                    if not dependencies[candidate] and candidate not in [x["name"] for x in ordered] and candidate not in ready:
                        ready.append(candidate)
            ready.sort()
        if len(ordered) != len(recipe_by_name):
            remaining = sorted(name for name, deps in dependencies.items() if deps)
            raise ConfigurationError("Recipe dependency cycle: " + ", ".join(remaining))
        return ordered


class Validator:
    def __init__(self, project: Any, config: dict[str, Any], folder_ids: dict[str, str] | None = None):
        self.project, self.config = project, config
        self.folder_ids = folder_ids or {}

    def _links_match(self, wanted: dict[str, Any], actual: dict[str, Any]) -> bool:
        """Compare declared recipe links, ignoring DSS-managed metadata.

        DSS augments items in a raw recipe definition (for example with
        dependencies and partitioning information) when a recipe is saved.
        Those fields are not part of the declarative link contract and vary by
        recipe type and DSS version.  The stable contract is the input role and
        the referenced object(s), including duplicate links when present.
        """
        project_key = getattr(self.project, "project_key", None)

        def normalize(ref: str) -> str:
            prefix = f"{project_key}." if project_key else ""
            ref = ref[len(prefix):] if prefix and ref.startswith(prefix) else ref
            return self.folder_ids.get(ref, ref)

        def references(roles: dict[str, Any]) -> dict[str, list[str]] | None:
            if not isinstance(roles, dict):
                return None
            result = {}
            for role, link in roles.items():
                items = link.get("items") if isinstance(link, dict) else None
                if not isinstance(items, list) or any(
                    not isinstance(item, dict) or not isinstance(item.get("ref"), str) for item in items
                ):
                    return None
                result[role] = sorted(normalize(item["ref"]) for item in items)
            return result

        expected = references(wanted)
        observed = references(actual)
        return expected == observed

    def validate(self) -> None:
        expected_datasets = {x["name"] for x in self.config["datasets"]}
        actual_datasets = {x.name for x in self.project.list_datasets()}
        expected_recipes = {x["name"] for x in self.config["recipes"]}
        actual_recipes = {x.name for x in self.project.list_recipes()}
        expected_folders = {x["name"] for x in self.config["folders"]}
        actual_folders = {x["name"] if isinstance(x, dict) else x.name
                          for x in self.project.list_managed_folders()}
        missing = (expected_datasets - actual_datasets) | (expected_recipes - actual_recipes) | (expected_folders - actual_folders)
        if missing:
            raise ConfigurationError("DSS did not create expected objects: " + ", ".join(sorted(missing)))
        for dataset_spec in self.config["datasets"]:
            if dataset_spec.get("generated_sample") or dataset_spec.get("files"):
                uploaded = self.project.get_dataset(dataset_spec["name"]).uploaded_list_files()
                if not uploaded:
                    raise ConfigurationError(f"Uploaded dataset has no files: {dataset_spec['name']}")
        for model_spec in self.config.get("models", []):
            folder_id = self.folder_ids.get(model_spec["folder"], model_spec["folder"])
            contents = self.project.get_managed_folder(folder_id).list_contents().get("items", [])
            expected_file = model_spec.get("destination", "/" + model_spec.get("filename", "linear_regression.pkl"))
            if expected_file not in {item.get("path") for item in contents}:
                raise ConfigurationError(
                    f"Generated model file is missing: {model_spec['folder']}{expected_file}"
                )
        for recipe_spec in self.config["recipes"]:
            actual = self.project.get_recipe(recipe_spec["name"]).get_settings().get_recipe_raw_definition()
            expected = recipe_spec.get("definition", {})
            for direction in ("inputs", "outputs"):
                wanted = expected.get(direction, {})
                if not self._links_match(wanted, actual.get(direction, {})):
                    raise ConfigurationError(
                        f"Recipe {direction} differ after creation: {recipe_spec['name']}"
                    )
        for spec in self.config["datasets"]:
            if spec.get("zone") and self._zone_name(self.project.get_dataset(spec["name"])) != spec["zone"]:
                raise ConfigurationError(f"Flow Zone differs after creation: {spec['name']}")
        for spec in self.config["recipes"]:
            if spec.get("zone") and self._zone_name(self.project.get_recipe(spec["name"])) != spec["zone"]:
                raise ConfigurationError(f"Flow Zone differs after creation: {spec['name']}")
        for spec in self.config["folders"]:
            if spec.get("zone"):
                folder_id = self.folder_ids.get(spec["name"], spec["name"])
                if self._zone_name(self.project.get_managed_folder(folder_id)) != spec["zone"]:
                    raise ConfigurationError(f"Flow Zone differs after creation: {spec['name']}")
        LOG.info("Validation passed: %d datasets, %d folders, %d recipes", len(expected_datasets), len(expected_folders), len(expected_recipes))

    def _zone_name(self, obj: Any) -> str:
        """Look up ownership through Flow; recipes do not expose get_zone()."""
        return self.project.get_flow().get_zone_of_object(obj).name


class TemplateProjectEngine:
    """Creates a new project only; it never mutates an existing project."""
    def __init__(self, client: Any, project_key: str, project_name: str, config: dict[str, Any], config_dir: Any):
        self.client, self.project_key, self.project_name = client, project_key, project_name
        self.config, self.config_dir = config, config_dir

    def run(self) -> Any:
        recipe_specs = DependencyResolver(self.config).resolve_recipes()
        projects = self.client.list_projects()
        if any((x.get("projectKey") if isinstance(x, dict) else x.project_key) == self.project_key
               for x in projects):
            raise ConfigurationError(f"Project already exists: {self.project_key}; refusing to alter it")
        with tempfile.TemporaryDirectory(prefix="dss-template-") as temp_dir:
            generated_dir = Path(temp_dir)
            sample_files = SampleDataGenerator(self.config.get("sample_data", []), generated_dir / "data").generate()
            model_files = ModelGenerator(self.config.get("models", []), sample_files, generated_dir / "models").generate()
            project = self._create(project_key=self.project_key, project_name=self.project_name,
                                   recipe_specs=recipe_specs, sample_files=sample_files, model_files=model_files)
        return project

    def _create(self, project_key: str, project_name: str, recipe_specs: list[dict[str, Any]],
                sample_files: dict[str, Any], model_files: list[tuple[str, str, Any]]) -> Any:
        project = ProjectCreator(self.client, project_key, project_name, self.config.get("project", {})).create()
        zones = ZoneCreator(project, self.config["zones"]).create()
        # Connections are instance-wide rather than project objects, but are
        # provisioned here before any dataset/folder references them.
        ConnectionCreator(self.client, self.config["connections"]).create()
        datasets = DatasetCreator(project, self.config["datasets"], self.config_dir, sample_files).create()
        folders = FolderCreator(project, self.config["folders"], self.config_dir).create()
        for folder_name, destination, source in model_files:
            with source.open("rb") as stream:
                folders[folder_name].put_file(destination, stream)
            LOG.info("Uploaded generated model %s to managed folder %s", destination, folder_name)
        folder_ids = {name: folder.id for name, folder in folders.items()}
        recipes = RecipeCreator(project, recipe_specs, self.config_dir, folder_ids).create()
        # Placing output objects also moves their generating recipe, then explicit
        # recipe placement below makes the desired ownership unambiguous.
        objects = {**datasets, **folders, **recipes}
        for spec in self.config["datasets"] + self.config["folders"] + self.config["recipes"]:
            if spec.get("zone"):
                zones[spec["zone"]].add_item(objects[spec["name"]])
        Validator(project, self.config, folder_ids).validate()
        return project
