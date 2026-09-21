"""Studio access to the portable mapper's single creative-plan contract."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_path = Path(__file__).resolve().parents[2] / "skills" / "beat-saber-mapping" / "scripts" / "mapping_plan.py"
_spec = spec_from_file_location("_beatforge_mapping_plan", _path)
if _spec is None or _spec.loader is None:
    raise ImportError("BeatForge mapping plan module is missing")
_module = module_from_spec(_spec)
_spec.loader.exec_module(_module)
normalize_mapping_plan = _module.normalize_mapping_plan
build_section_plan = _module.build_section_plan
