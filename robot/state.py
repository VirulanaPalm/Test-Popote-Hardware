"""Conversation states and configurable idle behavior transitions."""

from __future__ import annotations

import time
from enum import Enum


class RobotState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    BORED = "BORED"
    SLEEPY = "SLEEPY"
    SLEEPING = "SLEEPING"
    CURIOUS = "CURIOUS"
    ANNOYED = "ANNOYED"
    EXCITED = "EXCITED"
    ERROR = "ERROR"


class RobotStateMachine:
    def __init__(self, idle_settings: dict, on_change=None, clock=time.monotonic):
        self.idle_settings = idle_settings
        self.on_change = on_change
        self.clock = clock
        self.current = RobotState.IDLE
        self.last_interaction = clock()

    def set(self, next_state: RobotState, interaction=False):
        if interaction:
            self.last_interaction = self.clock()
        if next_state is self.current:
            return
        self.current = next_state
        if self.on_change:
            self.on_change(next_state)

    def update_idle(self):
        if self.current not in {
            RobotState.IDLE, RobotState.BORED, RobotState.SLEEPY,
            RobotState.SLEEPING,
        }:
            return
        elapsed = self.clock() - self.last_interaction
        bored_at = float(self.idle_settings.get("bored_after_seconds", 20))
        sleepy_at = float(self.idle_settings.get("sleepy_after_seconds", 45))
        sleeping_at = float(self.idle_settings.get("sleeping_after_seconds", 90))
        if elapsed >= sleeping_at:
            target = RobotState.SLEEPING
        elif elapsed >= sleepy_at:
            target = RobotState.SLEEPY
        elif elapsed >= bored_at:
            target = RobotState.BORED
        else:
            target = RobotState.IDLE
        self.set(target)

    def cancel_listening(self):
        self.set(RobotState.IDLE)
        self.last_interaction = self.clock()
