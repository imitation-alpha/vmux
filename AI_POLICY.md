# AI-Assisted Contribution Policy

AI-assisted contributions are welcome. The human contributor remains the
author and owner of everything submitted under their name.

## Contributor responsibilities

Before submitting AI-assisted work, you must:

- Read and understand every changed line and be able to explain the design and
  behavior without delegating review discussion to an agent.
- Personally review the output for correctness, security, privacy, and fit with
  vmux's local-first scope.
- Run and report the relevant automated and manual verification.
- Confirm that code, text, images, fixtures, and other material may legally be
  submitted under the project's license. Do not assume generated output has
  clean provenance.
- Remove fabricated APIs, tests that do not test the claimed behavior, stale
  comments, unnecessary dependencies, and unrelated generated changes.
- Keep secrets, private pane contents, vulnerability details, personal data,
  and third-party confidential material out of prompts and tool inputs.

The contributor, not the tool provider or model, is accountable for licensing,
security, testing, correctness, maintenance, and responses to review.

## Disclosure

In the pull request, state whether AI materially assisted the implementation.
If it did, briefly describe what it helped produce and how you reviewed and
verified the result. Prompt transcripts, chat logs, and model names are not
required.

Minor spelling suggestions, formatting, or editor completion do not need a
special disclosure. Generated or substantially rewritten code, tests,
documentation, designs, or implementation plans do.

## Review and enforcement

AI assistance does not lower the contribution, testing, security, or review
bar. Review conversations must be handled by the human contributor. Fully
autonomous or evidently unreviewed submissions, repeated generated noise, and
submissions the contributor cannot explain may be closed.
