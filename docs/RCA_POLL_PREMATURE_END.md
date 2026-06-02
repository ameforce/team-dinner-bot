# RCA: premature booking completion guard

This note records a fixed workflow issue where a booking completion action could
be accepted before the poll had reached the booking-assigned state.

## Symptom

A user-facing booking-complete message could be emitted while the workflow was
still in an earlier poll state.

## Root Cause

The booking completion handler only rejected already-finished runs. It did not
strictly require the run to be in `BOOKING_ASSIGNED` before accepting the
booking completion action.

## Fix

`WorkflowEngine.on_booking_done()` now accepts booking completion only from the
`BOOKING_ASSIGNED` state. Earlier states return a not-ready message instead of
marking the workflow done.

Forced poll replacement also closes any existing open run before creating the
new one, which prevents stale poll state from leaking into the next scenario.

## Regression Coverage

The scenario tests cover:

- rejecting booking completion before assignment
- replacing an open poll during a forced run
- keeping poll-close scheduling aligned with the active run
