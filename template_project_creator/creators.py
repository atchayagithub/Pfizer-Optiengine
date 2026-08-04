"""Public-API-only creators for each DSS object type."""
from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any

from .config import ConfigurationError

LOG = logging.getLogger(__name__)


def _update(mapping: dict, updates: dict | None) -> None:
    """Deep copy user data into the mutable dict exposed by a DSS settings API."""
    if updates:
        mapping.update(copy.deepcopy(updates))


def _apply_common_settings(obj: Any, spec: dict[str, Any]) -> None:
    settings = obj.get_settings()
    raw = settings.get_raw()
    _update(raw, spec.get("settings"))
    if "description" in spec:
        raw["description"] = spec["description"]
    if "tags" in spec:
        raw["tags"] = list(spec["tags"])
    settings.save()


class ProjectCreator:
    def __init__(self, client: Any, project_key: str, project_name: str, spec: dict[str, Any]):
        self.client, self.project_key, self.project_name, self.spec = client, project_key, project_name, spec

    def create(self) -> Any:
        owner = self.spec.get("owner")
        if not owner:
            auth = self.client.get_auth_info()
            owner = auth.get("authIdentifier") or auth.get("login")
        if not owner:
            raise ConfigurationError("project.owner is required when the API cannot determine the authenticated user")
        LOG.info("Creating project %s", self.project_key)
        project = self.client.create_project(self.project_key, self.project_name, owner)
        _apply_common_settings(project, self.spec)
        return project


class ConnectionCreator:
    def __init__(self, client: Any, specs: list[dict[str, Any]]):
        self.client, self.specs = client, specs

    def create(self) -> None:
        existing = {x if isinstance(x, str) else x.get("name") for x in self.client.list_connections()}
        for spec in self.specs:
            if spec["name"] in existing:
                if not spec.get("allow_existing", False):
                    raise ConfigurationError(f"Connection already exists: {spec['name']}")
                LOG.info("Using existing connection %s", spec["name"])
                continue
            if "type" not in spec:
                raise ConfigurationError(f"Connection {spec['name']} requires type")
            LOG.info("Creating instance connection %s", spec["name"])
            self.client.create_connection(
                spec["name"], spec["type"], copy.deepcopy(spec.get("params", {})),
                spec.get("usable_by", "ALL"), spec.get("allowed_groups", []),
            )


class ZoneCreator:
    def __init__(self, project: Any, specs: list[dict[str, Any]]):
        self.project, self.specs = project, specs

    def create(self) -> dict[str, Any]:
        flow, zones = self.project.get_flow(), {}
        for spec in self.specs:
            zone = flow.create_zone(spec["name"], spec.get("color", "#2ab1ac"))
            settings = zone.get_settings()
            raw = settings.get_raw()
            _update(raw, spec.get("settings"))
            settings.save()
            zones[spec["name"]] = zone
            LOG.info("Created Flow Zone %s", spec["name"])
        return zones


class DatasetCreator:
    def __init__(self, project: Any, specs: list[dict[str, Any]], config_dir: Path,
                 generated_files: dict[str, Path] | None = None):
        self.project, self.specs, self.config_dir = project, specs, config_dir
        self.generated_files = generated_files or {}

    def create(self) -> dict[str, Any]:
        result = {}
        for spec in self.specs:
            name, kind = spec["name"], spec.get("kind", "filesystem").lower()
            if kind == "filesystem":
                obj = self.project.create_filesystem_dataset(name, spec["connection"], spec["path"])
            elif kind in {"uploaded", "upload"}:
                if not spec.get("connection"):
                    raise ConfigurationError(
                        f"Uploaded dataset {name} requires an explicit writable upload connection"
                    )
                obj = self.project.create_upload_dataset(name, spec["connection"])
            elif kind in {"managed", "managed_dataset"}:
                if not spec.get("connection"):
                    raise ConfigurationError(f"Managed dataset {name} requires a connection")
                # A flow output must be managed by DSS so an overwrite build
                # may clear and replace its contents.  Creating a regular
                # filesystem/S3 dataset with a fixed path makes it external,
                # and DSS deliberately refuses to clear it.
                builder = self.project.new_managed_dataset(name)
                builder.with_store_into(
                    spec["connection"], spec.get("type_option_id"), spec.get("format_option_id")
                )
                obj = builder.create()
            elif kind in {"azure_blob", "azure", "adls"}:
                obj = self.project.create_azure_blob_dataset(name, spec["connection"], spec["path"], spec.get("container"))
            elif kind in {"sql", "snowflake"}:
                sql_type = spec.get("sql_type", "Snowflake" if kind == "snowflake" else spec.get("type"))
                if not sql_type:
                    raise ConfigurationError(f"SQL dataset {name} requires sql_type")
                obj = self.project.create_sql_table_dataset(
                    name, sql_type, spec["connection"], spec["table"], spec["schema"], spec.get("catalog"))
            elif kind == "fslike":
                obj = self.project.create_fslike_dataset(
                    name, spec["dataset_type"], spec["connection"], spec["path"], spec.get("extra_params"))
            else:
                raise ConfigurationError(f"Unsupported dataset kind {kind!r} for {name}; use fslike for other file stores")
            _apply_common_settings(obj, spec)
            if spec.get("generated_sample"):
                sample = self.generated_files.get(spec["generated_sample"])
                if sample is None:
                    raise ConfigurationError(
                        f"Dataset {name} references unknown generated sample {spec['generated_sample']!r}"
                    )
                with sample.open("rb") as stream:
                    obj.uploaded_add_file(stream, spec.get("generated_filename", sample.name))
                LOG.info("Uploaded generated sample %s to dataset %s", sample.name, name)
            if kind in {"uploaded", "upload"}:
                for file_spec in spec.get("files", []):
                    source = (self.config_dir / file_spec["source"]).resolve()
                    if not source.is_file() or self.config_dir not in source.parents:
                        raise ConfigurationError(
                            f"Dataset file must be a file below the configuration directory: {source}"
                        )
                    with source.open("rb") as stream:
                        obj.uploaded_add_file(stream, file_spec.get("filename", source.name))
                    LOG.info("Uploaded seed file %s to dataset %s", source.name, name)
                # UploadedFiles datasets have no detected file format or schema
                # until this documented public-API operation is saved.
                if spec.get("files") or spec.get("generated_sample"):
                    obj.autodetect_settings().save()
                    LOG.info("Detected CSV format and schema for uploaded dataset %s", name)
            if "schema_columns" in spec:
                obj.set_schema({"columns": copy.deepcopy(spec["schema_columns"])})
            result[name] = obj
            LOG.info("Created dataset %s (%s)", name, kind)
        return result


class FolderCreator:
    def __init__(self, project: Any, specs: list[dict[str, Any]], config_dir: Path):
        self.project, self.specs, self.config_dir = project, specs, config_dir

    def create(self) -> dict[str, Any]:
        result = {}
        for spec in self.specs:
            try:
                folder = self.project.create_managed_folder(
                    spec["name"], spec.get("folder_type"), spec.get("connection", "managed_folders")
                )
            except Exception as exc:
                raise ConfigurationError(
                    f"Cannot create managed folder {spec['name']!r} on connection "
                    f"{spec.get('connection', 'managed_folders')!r}. Set folder_type to the "
                    f"DSS storage type (for a filesystem connection: Filesystem) and verify that "
                    f"the connection permits managed folders. DSS reported: {exc}"
                ) from exc
            _apply_common_settings(folder, spec)
            storage_params = copy.deepcopy(spec.get("storage_params", {}))
            if storage_params:
                settings = folder.get_settings()
                connection = storage_params.pop("connection", None)
                path = storage_params.pop("path", None)
                if connection is not None or path is not None:
                    settings.set_connection_and_path(connection, path)
                _update(settings.get_raw_params(), storage_params)
                settings.save()
            for file_spec in spec.get("files", []):
                source = (self.config_dir / file_spec["source"]).resolve()
                if not source.is_file() or self.config_dir not in source.parents:
                    raise ConfigurationError(f"Folder file must be a file below the configuration directory: {source}")
                destination = file_spec.get("destination", "/" + source.name)
                with source.open("rb") as stream:
                    folder.put_file(destination, stream)
                LOG.info("Uploaded %s to managed folder %s", destination, spec["name"])
            result[spec["name"]] = folder
            LOG.info("Created managed folder %s (%s)", spec["name"], folder.id)
        return result


class RecipeCreator:
    def __init__(self, project: Any, specs: list[dict[str, Any]], config_dir: Path,
                 folder_ids: dict[str, str] | None = None):
        self.project, self.specs, self.config_dir = project, specs, config_dir
        self.folder_ids = folder_ids or {}

    def _resolve_folder_references(self, definition: dict[str, Any]) -> dict[str, Any]:
        """Replace template folder names with the IDs assigned by DSS."""
        resolved = copy.deepcopy(definition)
        for direction in ("inputs", "outputs"):
            for role in resolved.get(direction, {}).values():
                if not isinstance(role, dict):
                    continue
                for item in role.get("items", []):
                    if isinstance(item, dict) and item.get("ref") in self.folder_ids:
                        item["ref"] = self.folder_ids[item["ref"]]
        return resolved

    def _render_code_template(self, source: str) -> str:
        """Render managed-folder IDs into code without hard-coding DSS IDs."""
        for name, folder_id in self.folder_ids.items():
            source = source.replace("${folder_id:" + name + "}", folder_id)
        return source

    def create(self) -> dict[str, Any]:
        result = {}
        for spec in self.specs:
            # create_recipe is the documented generic public API. "proto" permits
            # every native or plugin recipe type supported by the target DSS.
            proto = copy.deepcopy(spec.get("proto", {"name": spec["name"], "type": spec["type"]}))
            proto.setdefault("name", spec["name"])
            # The UI and recipe builder call this a "prepare" recipe, while
            # DSS's generic raw-recipe endpoint names its implementation shaker.
            if proto.get("type") == "prepare":
                proto["type"] = "shaker"
            proto.setdefault("type", spec.get("type"))
            creation_settings = copy.deepcopy(spec.get("creation_settings", {}))
            raw_payload = None
            if "code_template" in spec:
                source = (self.config_dir / spec["code_template"]).resolve()
                if not source.is_file() or self.config_dir not in source.parents:
                    raise ConfigurationError(
                        f"Recipe code template must be a file below the configuration directory: {source}"
                    )
                raw_payload = self._render_code_template(source.read_text(encoding="utf-8"))
            elif "payload" in spec:
                raw_payload = json.dumps(spec["payload"])

            # Create from the complete raw definition.  In particular, generic
            # create_recipe() may initialize only its default input role and
            # discard additional roles (such as a Python recipe's managed
            # folder input).  Raw mode is a public API feature and supplies the
            # complete declarative flow topology atomically at recipe creation.
            recipe_definition = copy.deepcopy(proto)
            _update(recipe_definition, spec.get("definition", {}))
            recipe_definition = self._resolve_folder_references(recipe_definition)
            # Raw creation does not add this default. DSS 14's Prepare runner
            # dereferences params.engineType while checking a recipe, so an
            # absent params object fails before the recipe can start.
            recipe_definition.setdefault("params", {})
            if proto.get("type") == "shaker":
                recipe_definition["params"].setdefault("engineType", "DSS")
            recipe = self._create_recipe(recipe_definition, creation_settings, raw_payload)
            settings = recipe.get_settings()
            definition = settings.get_recipe_raw_definition()
            self._assert_declared_links(spec, definition)
            # Creation settings are consumed by the create endpoint. Set the
            # payload again on the returned recipe so a following settings
            # save cannot replace code with an empty response payload. This is
            # also how a declarative Prepare payload (its transformation
            # steps) is persisted.
            if "code_template" in spec:
                settings.set_payload(raw_payload)
            elif "payload" in spec:
                if isinstance(spec["payload"], str):
                    settings.set_payload(spec["payload"])
                else:
                    settings.set_json_payload(copy.deepcopy(spec["payload"]))
            elif isinstance(creation_settings.get("payload"), str):
                settings.set_payload(creation_settings["payload"])
            if "description" in spec:
                definition["description"] = spec["description"]
            if "tags" in spec:
                definition["tags"] = list(spec["tags"])
            if "params" in spec:
                _update(settings.get_recipe_params(), spec["params"])
            settings.save()
            if "code_template" in spec:
                saved_payload = recipe.get_settings().get_payload()
                if saved_payload != raw_payload:
                    raise ConfigurationError(
                        f"Code payload was not saved for recipe {spec['name']}; refusing to create an empty recipe"
                    )
            result[spec["name"]] = recipe
            LOG.info("Created recipe %s (%s)", spec["name"], proto["type"])
        return result

    @staticmethod
    def _link_roles(definition: dict[str, Any], direction: str) -> dict[str, list[str]]:
        """Return recipe links by role, rejecting incomplete API responses."""
        roles = definition.get(direction, {})
        if not isinstance(roles, dict):
            return {}
        result = {}
        for role, link in roles.items():
            items = link.get("items") if isinstance(link, dict) else None
            if not isinstance(items, list) or any(not isinstance(item, dict) or not item.get("ref") for item in items):
                return {}
            result[role] = sorted(item["ref"] for item in items)
        return result

    def _assert_declared_links(self, spec: dict[str, Any], definition: dict[str, Any]) -> None:
        expected = self._resolve_folder_references(spec.get("definition", {}))
        for direction in ("inputs", "outputs"):
            if self._link_roles(definition, direction) != self._link_roles(expected, direction):
                raise ConfigurationError(
                    f"Recipe {spec['name']} does not contain all declared {direction} immediately after creation"
                )

    def _create_recipe(self, definition: dict[str, Any], creation_settings: dict[str, Any],
                       raw_payload: str | None) -> Any:
        """Create a recipe with its complete input/output topology in raw mode."""
        builder = self.project.new_recipe(definition["type"], definition["name"])
        builder.set_raw_mode()
        builder.recipe_proto = definition
        builder.creation_settings.update(creation_settings)
        # DSS requires a payload field for every raw recipe creation, including
        # visual recipes such as Prepare.  Recent DSS versions read `payload`;
        # older versions additionally honour `rawPayload`, so send both with
        # the same value for API-version compatibility.
        payload = raw_payload if raw_payload is not None else builder.creation_settings.get("payload", "")
        builder.creation_settings["payload"] = payload
        builder.creation_settings["rawPayload"] = payload
        return builder.create()
