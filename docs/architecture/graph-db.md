# 图数据库

Ambient Agent 弃用了传统的文件型 JSON 存储，全面采用以 SQLite 为后端的图数据库存储（保存在工作区下的 `graph.db` 之中）。这为 Widget 数据操作与智能体意图识别提供了可靠的事务支持与关系映射。

## 1. 数据库表结构

在 `backend/graph_db.py` 中，主要定义了四个基础表：

### A. 架构注册表 `graph_schemas`

存储已注册的实体类型约束：

- `id` (TEXT, PK): 唯一标识符（如 `"Task"`, `"Event"`, `"Note"`）。
- `name` (TEXT): 架构名称。
- `description` (TEXT): 架构描述。
- `properties` (TEXT): 字段与类型定义的 JSON 字符串（例如 `{"title": "String", "priority": "Integer", "completed": "Boolean"}`）。
- `is_core` (INTEGER): 是否为系统内置的三个核心架构（0/1）。
- `created_at` (TEXT): ISO-8601 创建时间。

### B. 节点数据表 `graph_nodes`

存储具体的实体节点：

- `id` (TEXT, PK): 节点唯一 UUID。
- `type` (TEXT): 指向 `graph_schemas.id` 的实体类别，有索引支持。
- `properties` (TEXT): 键值对数据的 JSON 序列化字符串。
- `namespace` (TEXT): 作用域命名空间，常用于隔离多用户数据。
- `created_at` (TEXT): ISO-8601 创建时间。

### C. 关系边数据表 `graph_edges`

存储节点之间的关联关系：

- `from_id` (TEXT): 起始节点 UUID。
- `to_id` (TEXT): 指向的目标节点 UUID。
- `type` (TEXT): 边类别（如 `"DEPENDS_ON"`, `"CREATED_BY"`）。
- `properties` (TEXT): 关系附加属性的 JSON 字符串。
- 联合主键：`(from_id, to_id, type)`，且对 `from_id` 与 `to_id` 分别建立索引。

### D. 变更历史表 `graph_mutation_history`

用于事务回滚（Rollback）与审计的操作历史：

- `id` (TEXT, PK): 变更唯一 ID。
- `session_id` (TEXT): 会话 ID。
- `forward_actions` (TEXT): 正向变更动作 JSON（用于审计）。
- `reverse_actions` (TEXT): 逆向变更动作 JSON（用于一键撤回 Rollback）。
- `snapshot_before` (TEXT): 变更前的快照。
- `pinned` (INTEGER): 用户是否标记该变更不可被超时自动清理（1）或 60 秒软默认过期清理（0）。

## 2. 内置核心架构

系统默认注入了三个核心架构，Widget 开发和 Agent 默认遵循以下契约：

| 架构名 (Schema ID) | 默认字段与类型定义                                                                 | 说明         |
| :----------------- | :--------------------------------------------------------------------------------- | :----------- |
| **Task**           | `title` (String), `completed` (Boolean), `priority` (Integer), `due_date` (String) | 待办事项实体 |
| **Event**          | `title` (String), `start_time` (String), `end_time` (String), `location` (String)  | 日历日程实体 |
| **Note**           | `title` (String), `content` (String), `tags` (String)                              | 便签备忘实体 |

自定义 Widget 可以调用后台 API 注册自己的 Schema，但存入 `graph_nodes` 之前必须进行**类型对齐（Schema Alignment）**校验，防止写入未定义或类型冲突的脏数据。
