#!/usr/bin/env python3
"""Quick tool to test and display live MIDI messages from an Elektron Digitakt."""
import sys
import time

try:
    import mido
except ImportError:
    print("mido is required: pip install mido python-rtmidi")
    sys.exit(1)

ports = mido.get_input_names()
dt_ports = [p for p in ports if "digitakt" in p.lower() or "elektron" in p.lower()]
port_name = dt_ports[0] if dt_ports else (ports[0] if ports else None)

if not port_name:
    print("No MIDI input ports found!")
    sys.exit(1)

print(f"Opening MIDI input: {port_name}")
print("Ready! Press Trig keys, turn Knobs A-H, or press PLAY/STOP on your Digitakt.")
print("Press Ctrl+C to exit.\n")

with mido.open_input(port_name) as inport:
    try:
        for msg in inport:
            if msg.type == "clock":
                continue  # Suppress MIDI clock flood
            print(f"  {msg}")
    except KeyboardInterrupt:
        print("\nDone.")
