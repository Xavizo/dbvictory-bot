"""Speed Up: recasts haste at a random moment before the effect ends, never every second."""
from dbv.brain.combat.speed_up import run_speed

run = run_speed  # the scheduling lives in dbv/brain/combat/speed_up.py
