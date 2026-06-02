# RCA: poll and settings hardening fixes

This note summarizes four product-readiness issues that were fixed before the
public release.

## Poll Date Count

The poll date generator previously produced fewer business-day options than the
Slack poll UI expected. The date-selection path now keeps the generated date
count and rendered button count aligned.

## Settings Modal

The settings modal had weak defaults and validation around schedule, booking
URL, and poll duration fields. The modal now initializes from persisted channel
settings when available, falls back to public defaults, and clamps poll duration
to the supported range.

## Scheduler Recovery

Restart recovery could leave open poll-close jobs without a fresh deadline. The
scheduler now re-arms open poll closes after process restart and normalizes
deadlines with the configured timezone.

## Forced Poll Runs

Forced runs could conflict with an already-open poll. Forced replacement now
finishes the existing open run before starting a new poll, avoiding duplicate
active workflow state.
