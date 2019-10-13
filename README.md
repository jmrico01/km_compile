# Kapricorn Media - Compile System

Shared compilation system/setup for Kapricorn Media projects.
- Creates all output files in `build/`
- Builds single entry point `src/main.cpp`
- Expects project-specific build info in `compile/app_info.py` (external libs, etc)
- Copies entire directory `data/` into `build/data/`

## TODO

- Project-specific compiler flags? May not be necessary
- More flexible `data` copying
