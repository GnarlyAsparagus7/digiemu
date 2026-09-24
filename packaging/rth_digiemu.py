"""PyInstaller runtime hook: load the bundled unicorn.dll and capstone.dll.

Both bindings try their environment variable FIRST (unicorn_py3/unicorn.py
builds its search list starting with LIBUNICORN_PATH; capstone/__init__.py
with LIBCAPSTONE_PATH), before the copy next to the package. A variable left
over from a developer shell or another tool could therefore load a stock
Unicorn, which emu.unicorn_compat refuses, or quietly drop the digiemu speed
patches, which nothing refuses (emu/native.py degrades). So before the entry
script runs, point both variables at the libraries in this bundle. Plain
assignment, not setdefault: whatever was set before is exactly what must not
win. Children started from the frozen exe run this hook again themselves.

Runs before packaging/digiemu_main.py and before anything imports unicorn.
"""
import os
import sys

_base = getattr(sys, '_MEIPASS', None)
if _base:
    os.environ['LIBUNICORN_PATH'] = os.path.join(_base, 'unicorn', 'lib')
    os.environ['LIBCAPSTONE_PATH'] = os.path.join(_base, 'capstone', 'lib')
