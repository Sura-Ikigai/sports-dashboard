# Deliberately empty.
#
# backend/model/ will be imported by the FastAPI app for inference once Phase 2 wires it up (D-016
# in docs/plans/PLAN-current.md). loader.py pulls in pandas/numpy -- training-only dependencies
# (backend/requirements-train.txt) that must never be installed into the served image. Keeping this
# file empty means `import model` (or any future serving-time submodule) never transitively imports
# loader.py or its heavy dependencies. Import submodules directly: `from model import loader`.
