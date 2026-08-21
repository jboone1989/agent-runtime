# Motivation / Executive / Planner / Policy boundary

Status: frozen design rule. First implementation is Runtime **v0.2.3 Executive Foundation**, after Life Loop Hardening. Do not implement this during Life Loop Integration.

## Target control path

```text
Perception / Memory Retrieval / World Beliefs
                    │
                    ▼
                Motivation
        what is worth attending, approaching, or avoiding
                    │
                    ▼
          Executive / Deliberation
   what to select, maintain, pause, or replan now
                    │
                    ▼
             Plan / PlanStep
      steps and capability requirements
                    │
                    ▼
        Capability Resolution (if there is a gap)
                    │
                    ▼
       Authorization / Budget / Consent
                    │
                    ▼
                Actuation
                    │
                    ▼
        Outcome / Experience / Learning
```

Language, math, programming, design, law, music, and job skills are Skill / Tool / Model / Workflow / Library / Service / Remote Agent. They are not new Runtime core modules.

## Motivation

Input is a controlled projection of subject state. Output is:

- valence / arousal / urgency
- threat / opportunity
- drives
- attention priorities
- approach/avoid, exploration, persistence, risk bias
- contract-level action tendencies
- learning signal
- self-model update proposals

Motivation must not:

- choose a concrete tool or remote Agent
- create an executable shell/API call
- bypass authorization
- commit long-term memory or learning
- decide a public commitment

Life Loop Integration keeps `MotivationEngine.process(...)`. `TendencyInterpreter.goals` and `available_capabilities` stay empty in that slice. Future executable context lives on **ExecutiveContext**, not extra `process()` arguments.

## Executive / Deliberation

Responsible for:

- contextual choice among goals, commitments, threats, and opportunities
- inhibit, pause, resume, and switch
- continue the current plan or request replanning
- working context and attention budget
- turning a capability gap into reuse / delegate / acquire / adapt / create
- future intentions and waiting conditions

v0.2.3 first models (deterministic, no LLM):

```text
src/agent_runtime/executive/     context, attention, working_context, controller, interruption, records
src/agent_runtime/planning/      plan, step, state, requirements, legacy_adapter
src/agent_runtime/time/          intention, deadline, waiting
src/agent_runtime/experience/    trace, refs
```

`LegacySimplePlannerAdapter` must keep calling existing `SimplePlanner` so v0.2.2 tests are not rewritten in one step. Persist the new records first; replace the planner algorithm later.

## Planner

Produces or revises a `Plan` from goals and Executive constraints. It does not authorize.

## Policy

Hard adjudication of Constitution, AutonomyPolicy, ResourceBudget, Consent, and SideEffect. Motivation, Learning, Survival, and Capability Need cannot raise the authorization ceiling.

## Attention (v0.2.3)

Deterministic budget only:

- unresolved high-severity threat may interrupt ordinary user plans
- resolved events lower interrupt priority
- `attention_priority` orders signals and memory retrieval
- each tick has a finite entry budget
- ignored signals keep a reference and reason; they do not vanish
- REST/FREEZE lowers new exploration budget; it does not delete an existing plan

VirtualBrain may produce attention *bias*. It does not own the Runtime Attention Controller.

## ExperienceTrace (v0.2.3)

Records Goal → Plan → Step → Decision → Action → Outcome with stable refs. Recording is not learning. v0.3 consumes traces.

## Causal references

New Goal, Plan, PlanStep, Action, Outcome, Experience, Memory, CapabilityGap, CapabilityDecision, and Network Task records must use stable refs, not free-text `reason` as the only causal link:

```text
record_id
agent_id
logical_cycle
observed_at
effective_at
caused_by[]
evidence_refs[]
confidence
```

## What this document does not authorize now

- implementing Executive during Life Loop Integration
- calling an LLM from the first Executive
- putting Memory / Goals / Plans / Commitments into MotivationInput without extension governance and a budgeted projection
- treating `SEEK_SUPPORT → record` as a real help-seeking capability (SimplePlanner compatibility only)
