# Dataiku DSS template-project creator

This is a new, standalone module. It does not import, call, or modify `copy_flow_zones.py` (the existing Flow Zone Synchronization Engine).

It creates a new DSS project declaratively through the official `dataiku-api-client` Python Public API. The engine uses public `DSSClient.create_project`, `create_connection`, project dataset/folder/recipe creators, managed-folder `put_file`, and Flow Zone `add_item` calls. No undocumented DSS endpoint or private client method is used.

## Install and run

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python create_template_project.py \
  --host https://dss.example.com \
  --api-key "$DSS_API_KEY" \
  --config configs/flight_template.yaml
```

`project.key` and `project.name` normally live in the configuration. The
optional `--project-key` and `--project-name` flags override them for a
one-off deployment.

If you do not know the managed-folder connection name on an instance, discover
the visible connection names without changing DSS (connection parameters and
secrets are not printed):

```bash
python3 create_template_project.py \
  --host http://localhost:11000 \
  --api-key "$DSS_API_KEY" \
  --list-connections
```

The API key needs project-creation rights. Creating instance connections additionally requires an administrator API key; mark an administrator-created connection `allow_existing: true` (and set `external_connection: true` on its dataset/folder use) when connection creation is not in scope.

## Configuration

`template_project_creator/config.schema.json` is the machine-readable base schema. `configs/flight_template.yaml` creates the four-zone Flight Prediction Template, `configs/employee_prediction_template.yaml` creates the Employee Prediction Template, and `configs/hospital_patient_analytics_template.yaml` creates Hospital Patient Analytics with four raw clinical sources, Prepare and Join processing, logistic-regression risk scoring, and dashboard reporting. Configuration paths in `folders[].files[].source` and `recipes[].code_template` are relative to the configuration file and cannot escape its directory.

The main fields are:

| Section | Purpose |
| --- | --- |
| `project` | owner (optional if DSS returns the authenticated user), tags, description, full public project settings |
| `connections` | instance connection name, DSS type, params, access controls |
| `zones` | zone name, colour, and optional settings |
| `datasets` | `uploaded`, `managed`, `filesystem`, `sql`, `snowflake`, `azure_blob`/`adls`, or `fslike`; connection/location or seed files, schema, tags, settings, zone |
| `folders` | managed-folder connection/type, files, metadata, settings, zone |
| `recipes` | type, generic `proto`, creation settings, complete raw definition, params/payload, zone |
| `sample_data` | named, deterministic synthetic fixture generator; includes `flight`, employee, and hospital fixtures |
| `models` | generated trained `linear_regression` or `logistic_regression` artifacts uploaded into a declared managed folder |

For a visual or code recipe, capture its complete public settings into `definition`, `params`, and (for visual recipe settings) `payload`. `definition.inputs` and `definition.outputs` retain all dataset/folder links, including links that cross zones. A Prepare recipe's `payload` is its full transformation-step JSON; it is saved after creation, so do not omit it when reproducing an existing Prepare recipe. Use `code_template` (or `creation_settings.payload`) for code recipes—the file contents are explicitly written back to the created recipe before settings are saved. References containing `PROJECT_KEY.object` are preserved as cross-project links. Raw recipe creation uses the documented public recipe builder and supports native and plugin recipe types supported by the target DSS, including Python, Prepare, Join, Group, Sync, Stack, SQL, and Spark recipes.

Managed folders are linked by their DSS ID, not their display name. In a code template, use `${folder_id:folder_name}` wherever the code must open a declared managed folder; the creator substitutes the ID returned by DSS. The Flight prediction recipe uses `${folder_id:models}` to load `linear_regression.pkl` from the same folder attached as its recipe input.

Dataset details:

- Runnable seed data: `kind: uploaded`, a **writable upload connection**, and `files: [{source, filename}]`. Files are uploaded to a DSS `UploadedFiles` raw dataset using the public `uploaded_add_file` API. The Flight example uses `dataiku-managed-storage` only for this seed dataset.
- Flow outputs: use `kind: managed` and a connection with **Allow managed datasets** enabled. DSS owns the generated location and can safely clear it for overwrite builds. Do not use a fixed-path `filesystem`/`fslike` dataset as a recipe output unless its connection explicitly permits external-dataset clearing.
- Filesystem: `kind: filesystem`, `connection`, `path`.
- SQL: `kind: sql`, `sql_type`, `connection`, `table`, `schema`, optional `catalog`.
- Snowflake: `kind: snowflake` with the same SQL location fields; it uses DSS type `Snowflake` unless `sql_type` overrides it.
- Azure Blob/ADLS: `kind: azure_blob` or `adls`, `connection`, `container`, `path`. For uncommon FSlike connection types use `kind: fslike`, `dataset_type`, `connection`, `path`, and optional `extra_params`.

The Flight configuration generates exactly 100 realistic flight rows at run
time, uploads `flight_data.csv` to its UploadedFiles dataset, trains a genuine
scikit-learn `LinearRegression` using `distance`, `delay`, and `cancelled`, and
uploads `linear_regression.pkl` to `models`. Its Python recipe templates live
under `configs/recipes/`. Change only YAML to create another template project.
The corresponding generated example artifacts are included under
`examples/flight_assets/`; they are illustrative only—the run always regenerates
them from configuration.

## Hospital Patient Analytics

Run the hospital template with the same public-API CLI:

```bash
python create_template_project.py \
  --host https://dss.example.com \
  --api-key "$DSS_API_KEY" \
  --config configs/hospital_patient_analytics_template.yaml
```

It generates 200 linked patients, 250 admissions and diagnoses, and 300
medication records; trains and uploads `patient_risk_model.pkl`; then creates
the configured Prepare, Join, risk-scoring, and dashboard Flow objects. The
`dataiku-managed-storage` connection in the example must be replaced if the
target DSS instance uses another writable uploaded/managed-data connection.

For managed folders on a non-default connection, explicitly set `folder_type` to the matching DSS storage type—for example, `Filesystem` for a filesystem connection or `S3` for Amazon S3. The Flight example uses its configured `dataiku-managed-storage` S3 connection for the `models` folder and the generated datasets. `folders[].storage_params` optionally reproduces the connection/path fields from a manually configured folder. Crucially, the selected connection must have **Allow managed folders** enabled by a DSS administrator.

## Lifecycle and safeguards

The engine is intentionally create-only: it rejects an existing project key and refuses to mutate it. It validates config references before DSS is changed, then performs this order:

1. Create the project and Flow Zones, then provision declared instance connections.
2. Generate declared CSV/model artifacts, then create datasets, managed folders, and optional folder files.
3. Create recipes from their public API definitions, topologically ordered from declared input/output links.
4. Restore the specified Flow Zone membership for datasets, folders, and recipes.
5. Verify expected objects and recipe input links.

Failures are logged and exit non-zero. DSS cannot atomically roll back a partially created project, so inspect and deliberately delete any failed project before retrying. The module intentionally does not build datasets or recipes during validation: it validates Flow structure without requiring source data access.
