# Hyperion Agent Actions Reference

Source: https://ide-tutorial.hyperai.di.uoa.gr/hyperion-agent/ (captured 2026-10-06).

Hyperion is the AI assistant inside the HyperAI IDE: it answers questions and builds application files for the user. This page documents how an agent talks to the IDE.

## Request Structure

The IDE sends agents JSON requests with two fields:

- `user_id`: UUID for session memory tracking
- `text`: the user's input message

## Response Format

Agents respond via Server-Sent Events (SSE), streaming JSON objects as `data:` lines followed by blank lines.

### Response Types

Text streaming — display assistant messages:

```
data: {"response": "text chunk"}
```

Chunks concatenate to create typing effects; the stream completes when the response closes.

Actions — perform IDE operations:

```
data: {"action": "action_name", "field": "value"}
```

Actions execute silently; pair them with `response` events to notify users.

## Available Actions

| Action | Required fields | Behaviour |
|--------|-----------------|-----------|
| `create_folder` | `path` | Creates a directory at the specified location |
| `delete_folder` | `path` | Removes a directory |
| `create_file` | `path`, `content` | Creates and opens the file in the editor |
| `edit_file` | `path`, `content` | Replaces the entire file, opens it in the editor |
| `delete_file` | `path` | Removes a file |

Path rules: paths are relative to the workspace root (never absolute or containing `..`). For delete and edit operations, providing only a file name triggers a workspace-wide search using the first match.

## Helper Functions

`read_file(path)` — retrieves file content via HTTP GET to `/api/agent/file?path=`:

- Returns `{"path": "actual/path", "content": "..."}`
- Raises `ReadFileError` on ambiguity or missing files

`validate_file(path)` — validates against the Native/Device Apps schemas via GET to `/api/agent/validation/file?path=`:

- Returns a validation report with `type`, `valid`, `errors[]`, `warnings[]`
- Raises `ValidateFileError` on failures (not for invalid files)

## Docker Configuration

Local development uses `http://localhost:3001/api`; Docker containers use `http://host.docker.internal:3001/api`.
