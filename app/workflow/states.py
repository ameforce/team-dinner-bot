# -*- coding: utf-8 -*-
from enum import StrEnum


class WorkflowState(StrEnum):
    IDLE = "IDLE"
    REMIND_POSTED = "REMIND_POSTED"
    POLL_OPEN = "POLL_OPEN"
    POLL_CLOSED = "POLL_CLOSED"
    BOOKING_ASSIGNED = "BOOKING_ASSIGNED"
    DONE = "DONE"
