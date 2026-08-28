# Entity motivation and Talking Box integration

The motivation layer uses explicit numeric interaction-control signals. Names
such as **drive**, **interest**, and **frustration** are convenient policy terms;
they are not claims that Jerry or another entity has human feelings,
consciousness, needs, or subjective experience.

`server/entity_motivation.py` contains the deterministic domain policy. Drives
are clamped to 0–1, short-lived affect expires, elapsed effects are capped, and
goal ties sort stably. Goals are proposals such as asking a follow-up,
conserving attention, or greeting a familiar person. A goal is removed when the
body has not explicitly declared every required capability from the shared
`server.deployments.Capability` vocabulary.

## Runtime boundary

`server/motivation_runtime.py` owns state in a process-local dictionary keyed by
entity ID. It performs no database or file writes. Restarting the API process
resets all motivation state; multiple API workers also have independent state.
This is deliberate while policy behavior is evaluated.

The adapter accepts only privacy-neutral signals derived from already-authorized
request context:

- recognized presence becomes `familiar_presence=true`, without an ID or name;
- anonymous, unknown, and probable presence becomes `anonymous_presence=true`;
- a valid, already-reported sleep duration may be copied as a number;
- time since the previous interaction is computed by the process-local store.

Candidate names/IDs, biometric scores and embeddings, transcripts, memories,
relationships, enrollment/profile data, and private content are never copied
into motivation state or goal output. Capabilities are accepted only from the
explicit `body_capabilities` list; unknown values are ignored.

The Pi now advertises `button`, `microphone`, and `speaker`. This declaration is
descriptive, not action authority. For Talking Box, up to three selected goals
are serialized into the existing LLM request as bounded, inspectable
conversational guidance. They cannot activate the microphone, move hardware,
write hidden state, or initiate another external call. The normal user-triggered
chat completion remains the only consumer.

## Current limitations

State resets on restart and is not shared between workers. The policy currently
consumes only interaction presence, timing, sleep duration, and declared
capabilities; it does not consume memories or unfinished commitments. Goal
tuning remains code/config work and should be evaluated before persistence or
autonomous embodiment is considered.
