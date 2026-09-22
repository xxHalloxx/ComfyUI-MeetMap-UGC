from .nodes import (
    NODE_CLASS_MAPPINGS as CORE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as CORE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .reference_nodes import (
    NODE_CLASS_MAPPINGS as REFERENCE_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as REFERENCE_NODE_DISPLAY_NAME_MAPPINGS,
)
from .github_ref_nodes import (
    NODE_CLASS_MAPPINGS as GITHUB_REF_NODE_CLASS_MAPPINGS,
    NODE_DISPLAY_NAME_MAPPINGS as GITHUB_REF_NODE_DISPLAY_NAME_MAPPINGS,
)

NODE_CLASS_MAPPINGS = {
    **CORE_NODE_CLASS_MAPPINGS,
    **REFERENCE_NODE_CLASS_MAPPINGS,
    **GITHUB_REF_NODE_CLASS_MAPPINGS,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    **CORE_NODE_DISPLAY_NAME_MAPPINGS,
    **REFERENCE_NODE_DISPLAY_NAME_MAPPINGS,
    **GITHUB_REF_NODE_DISPLAY_NAME_MAPPINGS,
}

# Optional workflow families must never prevent the core MeetMap H3 nodes from
# registering. They are loaded only when their own runtime dependencies exist.
_OPTIONAL_MODULES = (
    "gdrive_nodes",
    "voice_nodes",
    "scail_runtime_nodes",
    "output_nodes",
    "stream_nodes",
)
for _module_name in _OPTIONAL_MODULES:
    try:
        _module = __import__(
            f"{__package__}.{_module_name}",
            fromlist=["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"],
        )
        NODE_CLASS_MAPPINGS.update(getattr(_module, "NODE_CLASS_MAPPINGS", {}))
        NODE_DISPLAY_NAME_MAPPINGS.update(
            getattr(_module, "NODE_DISPLAY_NAME_MAPPINGS", {})
        )
    except Exception as _exc:
        print(
            f"[ComfyUI-MeetMap-UGC] Optional module {_module_name} disabled: "
            f"{type(_exc).__name__}: {_exc}"
        )

WEB_DIRECTORY = "./js"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
