# ADR-0003: Make the Scheduler the Council's Publication Authority

## Status

Accepted

## Context

Human questions are routed through an operator, auditor, and strategist so that
the visible answer benefits from investigation, review, and synthesis. The
initial implementation allowed every controller to call `answer_human_message`,
and that tool immediately persisted and published the first answer to council
chat.

This allowed an operator answer to become visible while the auditor was still
working. Later corrections could be recorded on the wall, but they could not
revise the already-published answer because the first answer won. The scheduler
still waited for all controller turns before marking the notification complete,
so notification completion and human-visible completion had different
boundaries.

Raw controller response text was also used as a fallback answer. A turn's prose
is not necessarily a reviewed council conclusion and must not silently bypass
the role workflow.

## Decision

The council scheduler is the sole authority that publishes answers to human
chat.

Controllers have these responsibilities:

1. The operator investigates current evidence and posts findings to the wall.
2. The auditor reviews those findings and posts corrections, uncertainty, or
   confirmation.
3. The strategist synthesizes the reviewed evidence and may record one proposed
   answer with `answer_human_message`.
4. Every targeted controller finishes its turn with `signal_done`.

`answer_human_message` records a strategist answer candidate on the durable
notification. It does not write chat history, publish an SSE chat event, or emit
the final council-message wall event. Calls from non-strategist controllers are
rejected by server-side authorization rather than relying only on prompts.

After all targeted turns finish, the scheduler reloads the durable notification
and validates the publication gate. Normal publication requires:

- every enabled controller targeted by the notification to have signaled done;
- no targeted controller error or timeout; and
- for a human question, a strategist-authored answer candidate.

Only after that gate passes does the scheduler persist and publish the answer
once and mark the notification complete. Raw backend response text is never a
fallback human answer.

If the gate does not pass, the existing retry policy applies. Exhausted retries,
controller failures, missing completion signals, and timeouts produce an
explicit failed or timed-out notification. A future degraded-answer mode may be
added, but it must be configured explicitly and label partial answers as
unreviewed.

This is reviewed synthesis, not yet voting-based consensus. Proposal voting and
future quorum policies may add another gate without changing scheduler ownership
of publication.

## Consequences

Positive:

- Humans do not receive an answer before the auditor has completed review.
- Auditor corrections are available when the strategist composes the answer.
- Chat publication and notification completion share one lifecycle boundary.
- Prompts cannot override role authorization accidentally.
- Controller failures are visible instead of being hidden by a plausible raw
  response.

Negative:

- Answers take as long as the slowest required council role.
- A failed required controller prevents a normal answer even when another
  controller has useful partial findings.
- The scheduler and notification model must track publication state and protect
  against duplicate publication during retries and recovery.
- Questions with no enabled strategist cannot complete normally.

