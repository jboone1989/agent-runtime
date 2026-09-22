# LivingRuntime Remote integration

LivingRuntime Remote is a separate lightweight remote capability product and repository:

`github.com/jboone1989/livingruntime-remote`

Agent Runtime does not own or vendor its plugin, Connector, relay, gateway, release workflows, or marketplace metadata.

## Relationship

```text
LivingRuntime Remote  -> bounded remote transport
Pi Remote             -> persistent coding runtime
Agent Runtime         -> persistent agent lifecycle/capability orchestration
```

Agent Runtime may use LivingRuntime Remote as an external capability when remote machine access is needed, and may use Pi Remote separately for durable coding sessions.

## Source of truth

For Remote capabilities, installation, release status, security policy, tests, and public distribution, use the LivingRuntime Remote repository. Do not recreate a local capability registry or release checklist here.

## Integration principle

Agent Runtime integrations should depend on stable Remote protocol/capability boundaries rather than importing Remote implementation files. Agent Runtime must continue to boot when Remote is absent.
