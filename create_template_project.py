#!/usr/bin/env python3
"""CLI entry point for template_project_creator."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from dataikuapi import DSSClient

from template_project_creator.config import ConfigurationError, load_config
from template_project_creator.engine import TemplateProjectEngine


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a DSS project from a declarative template")
    parser.add_argument("--host", required=True, help="DSS host URL")
    parser.add_argument("--api-key", required=True, help="DSS API key")
    parser.add_argument("--project-key", help="Overrides project.key in the configuration")
    parser.add_argument("--project-name", help="Overrides project.name in the configuration")
    parser.add_argument("--config", help="JSON or YAML template configuration")
    parser.add_argument("--list-connections", action="store_true",
                        help="List visible DSS connections and exit (no project changes)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        client = DSSClient(args.host, args.api_key)
        if args.list_connections:
            for connection in client.list_connections():
                if isinstance(connection, str):
                    print(connection)
                    continue
                # Do not print params: connection definitions may contain secrets.
                if not isinstance(connection, dict):
                    print(str(connection))
                    continue
                visible = {key: value for key, value in connection.items()
                           if key.lower() in {"name", "type", "allowwrite", "allowmanagedfolders",
                                              "allowmanageddatasets", "usableby", "allowedgroups"}}
                print(json.dumps(visible or {"name": connection.get("name", "<unknown>")}, sort_keys=True))
            return 0
        if not args.config:
            raise ConfigurationError("--config is required unless --list-connections is used")
        config, config_dir = load_config(args.config)
        project_spec = config.get("project", {})
        project_key = args.project_key or project_spec.get("key")
        project_name = args.project_name or project_spec.get("name")
        if not project_key or not project_name:
            raise ConfigurationError(
                "project.key and project.name are required (or pass --project-key and --project-name)"
            )
        TemplateProjectEngine(client, project_key, project_name, config, config_dir).run()
    except Exception as exc:
        logging.getLogger(__name__).error("Project creation failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
