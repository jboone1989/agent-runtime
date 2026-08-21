# Contract v1 extension governance

Status: frozen design rule. Contract v1 **root is permanently frozen**. First code is a registry/validator during Life Loop Hardening. Do not add Memory / Goals / Plans / Commitments to MotivationInput in the current Life Loop slice.

## Allowed growth

```text
extensions.<owner>.<feature>
```

Each extension needs:

```python
@dataclass(frozen=True)
class ExtensionDescriptor:
    namespace: str
    version: str
    owner: str
    critical: bool
    schema_id: str
```

## Rules

- Namespace must not be a bare public name such as `memory` or `goals`.
- Unrecognized and `critical=false`: ignore.
- Unrecognized and `critical=true`: reject explicitly.
- Extensions must not carry secrets, provider handles, or live objects.
- Extensions must not change existing root-field semantics.
- If two or more independent consumers must understand a field, design Contract v2 instead of hiding it forever in an extension.
- Extension payload must be independently schema-validatable.

Current Life Loop does not pass full Memory, Goals, Plans, or Commitments. Later ticks pass budgeted projections, never Runtime internal objects.

## Hardening tests (when implemented)

- unknown non-critical extension ignored
- unknown critical extension rejected
- namespace collision rejected
- extension schema failure rejected
- root shape digest unchanged

Life Loop Integration still uses empty `extensions` on the Runtime-owned contract path, except VirtualBrain research mapping inside the mind (`extensions.virtualbrain` on research input). Do not add new Contract v1 root fields.
