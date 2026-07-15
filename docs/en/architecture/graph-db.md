# Graph Database (GraphDB)

Ambient Agent stores node relations in a local SQLite database (`graph.db`) in the workspace, bypassing legacy file-based JSON storage to guarantee robust transactional mutations.

## 1. Table Schema Design

Managed in `backend/graph_db.py`, the Graph Database relies on four main SQLite tables:

### A. Schemas Table (`graph_schemas`)

Defines the registered types of graph nodes:

- `id` (TEXT, PK): The schema identifier (e.g. `"Task"`, `"Event"`, `"Note"`).
- `name` (TEXT): Title of the schema.
- `description` (TEXT): Description of properties.
- `properties` (TEXT): JSON string mapping properties to types (e.g. `{"title": "String", "priority": "Integer"}`).

### B. Nodes Table (`graph_nodes`)

Stores all entity instances:

- `id` (TEXT, PK): Unique UUID.
- `type` (TEXT): Pointer to schema identifier.
- `properties` (TEXT): JSON serialized values.
- `namespace` (TEXT): Used to isolate user spaces.

### C. Edges Table (`graph_edges`)

Maps the relationship connections:

- `from_id` (TEXT) / `to_id` (TEXT): Start and end node UUIDs.
- `type` (TEXT): Edge relation type (e.g., `"DEPENDS_ON"`).
- `properties` (TEXT): JSON serialized relationship metadata.

### D. Mutation History (`graph_mutation_history`)

- Supports rollbacks (`reverse_actions` payloads) and provides temporary commit history before cleanups.

## 2. Core Schemas

By default, the database seeds three core schemas:

| Schema ID | Properties & Types                                                                 | Description    |
| :-------- | :--------------------------------------------------------------------------------- | :------------- |
| **Task**  | `title` (String), `completed` (Boolean), `priority` (Integer), `due_date` (String) | Todo item      |
| **Event** | `title` (String), `start_time` (String), `end_time` (String), `location` (String)  | Calendar event |
| **Note**  | `title` (String), `content` (String), `tags` (String)                              | Note or memo   |
