#!/usr/bin/env python3
"""Copy selected Dataiku Flow zones, their objects, definitions and folder files.

Requires: pip install dataiku-api-client
Run: python copy_flow_zones.py --host https://dss.example --api-key "$DSS_API_KEY" \
       --source TEMPLATE --target OPTI_ENGINE --zones LO5 LO6

This copies *definitions*, schemas and managed-folder files; it deliberately does
not copy dataset data.  Existing target objects are left in place and reconciled.
"""
import argparse
import copy
import logging
import sys
from collections import defaultdict

from dataikuapi import DSSClient

LOG = logging.getLogger("zone-copy")


class ZoneCopyError(RuntimeError):
    """A source zone cannot be represented safely in the target project."""


def configure_logging():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def existing_names(project, method):
    """Return object names/ids from a list_* API that may return dicts or list items."""
    result = getattr(project, method)()
    if isinstance(result, dict):
        result = result.get("items", [])
    return {x.get("name", x.get("id")) if isinstance(x, dict) else x.name for x in result}


def object_kind(obj):
    """Use public object attributes/classes without importing version-specific classes."""
    name = obj.__class__.__name__.lower()
    if "dataset" in name:
        return "DATASET"
    if "managedfolder" in name:
        return "MANAGED_FOLDER"
    if "recipe" in name:
        return "RECIPE"
    return "UNSUPPORTED"


def object_id(obj, kind):
    return obj.name if kind in ("DATASET", "RECIPE") else obj.id


def zone_recipe_owner(zones_by_name, zone_names, recipe_name):
    """Return a selected zone which explicitly contains ``recipe_name``.

    The API has returned both Flow item objects and dictionaries for
    ``DSSFlowZone.items``.  Recipes do not reliably implement ``get_zone()``,
    so use this only as an ownership lookup -- never as a dependency walk.
    """
    owners = []
    for zone_name in zone_names:
        for item in getattr(zones_by_name[zone_name], "items", []):
            if isinstance(item, dict):
                kind = item.get("type", item.get("objectType", "")).upper()
                item_name = item.get("name", item.get("id"))
            else:
                kind = object_kind(item)
                item_name = getattr(item, "name", getattr(item, "id", None))
            if kind == "RECIPE" and item_name == recipe_name:
                owners.append(zone_name)
                break
    return owners


def selected_objects(source, zone_names):
    """Return only objects owned by requested zones; never traverse upstream."""
    zones_by_name = {z.name: z for z in source.get_flow().list_zones()}
    missing = set(zone_names) - set(zones_by_name)
    if missing:
        raise ZoneCopyError("Source flow zone(s) not found: " + ", ".join(sorted(missing)))
    result, memberships = defaultdict(dict), {}

    def add_owned(obj, kind):
        owner_zone = obj.get_zone().name
        oid = object_id(obj, kind)
        if owner_zone in zone_names:
            result[kind][oid] = obj
            memberships[(kind, oid)] = owner_zone
        else:
            LOG.info("Not copying %s %s: owned by non-selected zone %s", kind, oid, owner_zone)

    # Scan all computables. This does not follow dependencies; get_zone() is
    # simply an ownership lookup, so Raw data objects cannot enter the selection.
    for dataset in source.list_datasets(as_type="objects"):
        add_owned(dataset, "DATASET")
    for folder_info in source.list_managed_folders():
        add_owned(source.get_managed_folder(folder_info["id"]), "MANAGED_FOLDER")

    # DSSRecipe has no get_zone() in this API version. A recipe's Flow Zone is
    # derived from its output computables. Use the selected output's owner zone.
    selected_zone_by_output = {
        **{name: memberships[("DATASET", name)] for name in result["DATASET"]},
        **{folder_id: memberships[("MANAGED_FOLDER", folder_id)]
           for folder_id in result["MANAGED_FOLDER"]},
    }
    for recipe_item in source.list_recipes():
        recipe = recipe_item.to_recipe()
        definition = recipe.get_settings().get_recipe_raw_definition()
        output_zones = set()
        for role in definition.get("outputs", {}).values():
            for item in role.get("items", []):
                ref = item["ref"]
                if ref.startswith(source.project_key + "."):
                    ref = ref.split(".", 1)[1]
                if ref in selected_zone_by_output:
                    output_zones.add(selected_zone_by_output[ref])
        explicit_owners = zone_recipe_owner(zones_by_name, zone_names, recipe.name)
        if len(explicit_owners) == 1:
            # This is authoritative when DSS exposes Flow Zone item membership.
            recipe_name = recipe.name
            result["RECIPE"][recipe_name] = recipe
            memberships[("RECIPE", recipe_name)] = explicit_owners[0]
        elif len(output_zones) == 1:
            recipe_name = recipe.name
            result["RECIPE"][recipe_name] = recipe
            memberships[("RECIPE", recipe_name)] = output_zones.pop()
        elif len(output_zones) > 1:
            # The recipe is still wholly in the requested copy scope.  Some
            # recipe types can write to outputs in more than one selected zone;
            # copying neither would lose selected objects and their links.  If
            # the API did not give us its explicit zone, retain every selected
            # link and use a deterministic zone for the recipe itself.
            owner = sorted(output_zones)[0]
            LOG.warning("Recipe %s has outputs in selected zones %s but no explicit "
                        "zone membership; assigning it to %s", recipe.name,
                        sorted(output_zones), owner)
            result["RECIPE"][recipe.name] = recipe
            memberships[("RECIPE", recipe.name)] = owner
        else:
            # Fallback for recipes whose outputs are unsupported object types.
            if len(explicit_owners) > 1:
                LOG.warning("Recipe %s appears in multiple selected zones %s; using %s",
                            recipe.name, explicit_owners, explicit_owners[0])
                result["RECIPE"][recipe.name] = recipe
                memberships[("RECIPE", recipe.name)] = explicit_owners[0]

    LOG.info("Selected %d datasets, %d folders, %d recipes from zones: %s",
             len(result["DATASET"]), len(result["MANAGED_FOLDER"]), len(result["RECIPE"]),
             ", ".join(zone_names))
    for kind in ("DATASET", "MANAGED_FOLDER", "RECIPE"):
        for oid in sorted(result[kind]):
            LOG.info("Will copy %s %s from zone %s", kind, oid, memberships[(kind, oid)])
    return result, memberships, zones_by_name


def all_recipe_refs(recipe):
    definition = recipe.get_settings().get_recipe_raw_definition()
    refs = []
    for direction in ("inputs", "outputs"):
        for role in definition.get(direction, {}).values():
            refs.extend(item["ref"] for item in role.get("items", []))
    return refs


def report_external_dependencies(objects, source_key):
    """Report, but never copy, dependencies outside the selected zones."""
    local = set(objects["DATASET"]) | set(objects["MANAGED_FOLDER"])
    missing = set()
    for recipe in objects["RECIPE"].values():
        for ref in all_recipe_refs(recipe):
            bare = ref.split(".", 1)[-1] if ref.startswith(source_key + ".") else ref
            if bare not in local:
                missing.add(ref)
    if missing:
        LOG.info("Keeping %d non-selected dependency reference(s) in source project: %s",
                 len(missing), ", ".join(sorted(missing)))


def expose_source_dependencies(source, target, objects):
    """Expose source-only datasets/folders and keep their refs live in target.

    This requires permission to edit the source project's settings. References to
    a third project are deliberately left alone and must already be exposed.
    """
    selected = set(objects["DATASET"]) | set(objects["MANAGED_FOLDER"])
    source_datasets = existing_names(source, "list_datasets")
    source_folders = {x["id"] for x in source.list_managed_folders()}
    to_expose = set()
    for recipe in objects["RECIPE"].values():
        for ref in all_recipe_refs(recipe):
            project_key, oid = (source.project_key, ref)
            if "." in ref:
                project_key, oid = ref.split(".", 1)
            if project_key != source.project_key or oid in selected:
                continue
            if oid in source_datasets:
                to_expose.add(("DATASET", oid))
            elif oid in source_folders:
                to_expose.add(("MANAGED_FOLDER", oid))
            else:
                LOG.warning("Cannot automatically expose unsupported external reference %s", ref)
    if to_expose:
        settings = source.get_settings()
        for object_type, object_id in sorted(to_expose):
            LOG.info("Exposing source %s %s to %s", object_type, object_id, target.project_key)
            settings.add_exposed_object(object_type, object_id, target.project_key)
        settings.save()


def connection_names(client):
    """DSS has returned both a list of strings and dict list-items across versions."""
    values = client.list_connections()
    return {v if isinstance(v, str) else v.get("name") for v in values}


def find_connection_names(value, found=None):
    """Find connection references in a raw dataset/folder definition."""
    found = found if found is not None else set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "connection" and isinstance(child, str) and child:
                found.add(child)
            else:
                find_connection_names(child, found)
    elif isinstance(value, list):
        for child in value:
            find_connection_names(child, found)
    return found


def assert_connection_settings(source_obj, target_obj, label):
    """Fail immediately if saving settings lost a source connection reference."""
    expected = find_connection_names(source_obj.get_settings().get_raw())
    actual = find_connection_names(target_obj.get_settings().get_raw())
    if expected != actual:
        raise ZoneCopyError(
            "%s connection settings were not preserved; expected %s, found %s" %
            (label, sorted(expected), sorted(actual))
        )
    if expected:
        LOG.info("Verified %s connection(s): %s", label, ", ".join(sorted(expected)))


def validate_connections(client, objects):
    available, required = connection_names(client), set()
    for kind in ("DATASET", "MANAGED_FOLDER"):
        for obj in objects[kind].values():
            required |= find_connection_names(obj.get_settings().get_raw())
    absent = required - available
    if absent:
        raise ZoneCopyError("Connection(s) do not exist on this DSS instance: " + ", ".join(sorted(absent)))
    LOG.info("Validated %d connection reference(s)", len(required))


def rewrite(value, replacements, source_key=None, target_key=None):
    """Deep-copy a DSS JSON definition and replace only exact object references."""
    if isinstance(value, str):
        # Keep non-copied SOURCE_PROJECT.object refs unchanged: they are live
        # cross-project dependencies, not objects to duplicate in the target.
        return replacements.get(value, value)
    if isinstance(value, list):
        return [rewrite(x, replacements, source_key, target_key) for x in value]
    if isinstance(value, dict):
        return {k: rewrite(v, replacements, source_key, target_key) for k, v in value.items()}
    return value


def strip_identity(raw):
    raw = copy.deepcopy(raw)
    for key in ("id", "projectKey", "project_key", "name"):
        raw.pop(key, None)
    return raw


def replace_raw_settings(target_obj, source_raw, replacements, source_key, target_key):
    """Copy settings while retaining DSS-assigned identity fields on target.

    `get_raw()` includes projectKey (and sometimes id/name). Those fields belong
    to the target object and are mandatory when `save()` sends the definition.
    """
    target_settings = target_obj.get_settings()
    target_raw = target_settings.get_raw()
    target_identity = {key: copy.deepcopy(target_raw[key]) for key in
                       ("id", "projectKey", "project_key", "name") if key in target_raw}
    desired = rewrite(strip_identity(source_raw), replacements, source_key, target_key)
    desired.update(target_identity)
    target_raw.clear()
    target_raw.update(desired)
    target_settings.save()


def copy_datasets(source, target, objects, replacements):
    existing = existing_names(target, "list_datasets")
    for name, src in objects["DATASET"].items():
        raw = src.get_settings().get_raw()
        if name in existing:
            dst = target.get_dataset(name)
            LOG.info("Reconciling existing dataset %s", name)
        else:
            LOG.info("Creating dataset %s", name)
            dst = target.create_dataset(name, raw["type"], copy.deepcopy(raw.get("params", {})),
                                        raw.get("formatType"), copy.deepcopy(raw.get("formatParams", {})))
        replace_raw_settings(dst, raw, replacements, source.project_key, target.project_key)
        assert_connection_settings(src, dst, "dataset %s" % name)
        # set_schema preserves declared schema; physical data/partitions are not copied.
        dst.set_schema(copy.deepcopy(src.get_schema()))


def copy_folders(source, target, objects, replacements):
    existing = existing_names(target, "list_managed_folders")
    folder_ids = {}
    for source_id, src in objects["MANAGED_FOLDER"].items():
        source_settings = src.get_settings()
        raw = source_settings.get_raw()
        name = raw.get("name", src.get_definition().get("name"))
        if name in existing:
            # Names are human labels; IDs are target-specific and must be remapped.
            dst = next(target.get_managed_folder(x["id"]) for x in target.list_managed_folders()
                       if x["name"] == name)
            LOG.info("Reconciling existing folder %s", name)
        else:
            LOG.info("Creating managed folder %s", name)
            # DSS validates this pair during POST. Passing only folder_type makes
            # DSS default to filesystem_folders, which fails for S3/Azure/ADLS.
            connection = source_settings.get_raw_params().get("connection")
            if not connection:
                raise ZoneCopyError(
                    "Managed folder %r has no connection in its settings; cannot create it safely" % name
                )
            dst = target.create_managed_folder(
                name,
                folder_type=source_settings.type,
                connection_name=connection,
            )
        replace_raw_settings(dst, raw, replacements, source.project_key, target.project_key)
        assert_connection_settings(src, dst, "managed folder %s" % name)
        folder_ids[source_id] = dst.id
    return folder_ids


def copy_folder_files(objects, target, folder_ids):
    for source_id, src in objects["MANAGED_FOLDER"].items():
        dst = target.get_managed_folder(folder_ids[source_id])
        for item in src.list_contents().get("items", []):
            path = item["path"]
            LOG.info("Copying folder %s file %s", source_id, path)
            response = src.get_file(path)
            response.raise_for_status()
            # `raw` is a streaming HTTP response accepted by the public put_file API.
            dst.put_file(path, response.raw)


def remap_recipe_definition(source, target, definition, objects, folder_ids):
    """Map a source recipe's topology to target objects or live source refs.

    DSS stores objects from the recipe's own project as bare names/IDs.  Once the
    recipe lives in target, a bare non-selected input would incorrectly mean
    TARGET_PROJECT.object.  Make it SOURCE_PROJECT.object instead.
    """
    source_datasets = existing_names(source, "list_datasets")
    source_folders = {x["id"] for x in source.list_managed_folders()}
    selected_datasets = set(objects["DATASET"])
    selected_folders = set(objects["MANAGED_FOLDER"])

    for direction in ("inputs", "outputs"):
        for role in definition.get(direction, {}).values():
            for item in role.get("items", []):
                original = item["ref"]
                project_key, object_id = source.project_key, original
                if "." in original:
                    project_key, object_id = original.split(".", 1)
                if project_key != source.project_key:
                    continue  # a third-project reference is already explicit
                if object_id in selected_datasets:
                    item["ref"] = object_id
                elif object_id in selected_folders:
                    item["ref"] = folder_ids[object_id]
                elif object_id in source_datasets or object_id in source_folders:
                    if direction == "outputs":
                        raise ZoneCopyError(
                            "Selected recipe %s writes to non-selected source object %s; "
                            "include its flow zone before copying." % (definition["name"], original)
                        )
                    item["ref"] = source.project_key + "." + object_id
                    LOG.info("Recipe %s input %s remains a live source reference",
                             definition["name"], item["ref"])
                else:
                    LOG.warning("Recipe %s has an unknown input/output reference %s",
                                definition["name"], original)
    return definition


def copy_recipes(source, target, objects, replacements, folder_ids):
    existing = existing_names(target, "list_recipes")
    for name, src in objects["RECIPE"].items():
        settings = src.get_settings()
        definition = rewrite(settings.get_recipe_raw_definition(), replacements,
                             source.project_key, target.project_key)
        definition["name"], definition["projectKey"] = name, target.project_key
        definition = remap_recipe_definition(source, target, definition, objects, folder_ids)
        payload = settings.get_payload()  # code is intentionally not rewritten.
        if name not in existing:
            LOG.info("Creating %s recipe %s", definition["type"], name)
            creator = target.new_recipe(definition["type"], name)
            creator.set_raw_mode()
            creator.recipe_proto = definition
            creator.creation_settings["rawPayload"] = payload
            dst = creator.create()
        else:
            LOG.info("Reconciling existing recipe %s", name)
            dst = target.get_recipe(name)
            dst_settings = dst.get_settings()
            # Existing recipe retains its identity but gets complete topology/settings.
            raw = dst_settings.get_recipe_raw_definition()
            raw.clear(); raw.update(definition)
            dst_settings.set_payload(payload)
            dst_settings.save()
        # Raw-mode creation already supplied all inputs/outputs; payload carries visual
        # settings or code. Calling save makes this explicit and version-safe.
        dst.get_settings().save()


def ensure_zones(source_flow, target_flow, zone_names, source_zones):
    target_zones = {z.name: z for z in target_flow.list_zones()}
    for name in zone_names:
        if name not in target_zones:
            LOG.info("Creating flow zone %s", name)
            target_zones[name] = target_flow.create_zone(name, color=source_zones[name].color)
    return target_zones


def restore_zone_membership(target, objects, memberships, target_zones, folder_ids):
    for kind in ("DATASET", "MANAGED_FOLDER", "RECIPE"):
        for oid in objects[kind]:
            obj = target.get_dataset(oid) if kind == "DATASET" else \
                  target.get_managed_folder(folder_ids[oid]) if kind == "MANAGED_FOLDER" else target.get_recipe(oid)
            obj.move_to_zone(target_zones[memberships[(kind, oid)]])


def validate_result(target, objects):
    for recipe_name in objects["RECIPE"]:
        status = target.get_recipe(recipe_name).get_status()
        # DSSRecipeStatus uses get_status_messages() (not get_messages()).
        messages = status.get_status_messages()
        errors = [m for m in messages if m.get("severity") == "ERROR"]
        if errors:
            LOG.warning("Recipe %s has DSS validation errors: %s", recipe_name, errors)
    LOG.info("Copy completed: %d datasets, %d folders, %d recipes", len(objects["DATASET"]),
             len(objects["MANAGED_FOLDER"]), len(objects["RECIPE"]))


def copy_flow_zones(host, api_key, source_key, target_key, zone_names, allow_external_dependencies=False):
    client = DSSClient(host, api_key)
    source, target = client.get_project(source_key), client.get_project(target_key)
    objects, memberships, source_zones = selected_objects(source, zone_names)
    report_external_dependencies(objects, source_key)
    validate_connections(client, objects)

    target_zones = ensure_zones(source.get_flow(), target.get_flow(), zone_names, source_zones)
    # Rewrite references only for objects copied by this run. All other
    # SOURCE_PROJECT.object references remain live source-project dependencies.
    replacements = {
        source_key + "." + name: target_key + "." + name
        for name in objects["DATASET"]
    }
    copy_datasets(source, target, objects, replacements)
    folder_ids = copy_folders(source, target, objects, replacements)
    replacements.update(folder_ids)
    replacements.update({source_key + "." + old_id: target_key + "." + new_id
                         for old_id, new_id in folder_ids.items()})
    # This is intentionally unconditional: non-selected upstream/downstream
    # objects are never copied, and selected recipes must use live source refs.
    expose_source_dependencies(source, target, objects)
    copy_folder_files(objects, target, folder_ids)
    copy_recipes(source, target, objects, replacements, folder_ids)
    restore_zone_membership(target, objects, memberships, target_zones, folder_ids)
    validate_result(target, objects)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="DSS base URL")
    parser.add_argument("--api-key", required=True, help="DSS API key")
    parser.add_argument("--source", required=True, help="Source project key")
    parser.add_argument("--target", required=True, help="Target project key")
    parser.add_argument("--zones", nargs="+", required=True, help="Flow zone names, e.g. LO5 LO6")
    parser.add_argument(
        "--allow-external-dependencies", action="store_true",
        help="Deprecated compatibility flag; non-selected dependencies are always source references.",
    )
    args = parser.parse_args()
    configure_logging()
    try:
        copy_flow_zones(args.host, args.api_key, args.source, args.target, args.zones,
                        args.allow_external_dependencies)
    except Exception as exc:
        LOG.exception("Flow-zone copy failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
