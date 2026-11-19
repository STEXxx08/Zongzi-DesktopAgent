from .config import MemoryConfig

__all__ = ["MemoryConfig", "MemoryManager"]


def __getattr__(name: str):
    if name == "MemoryManager":
        from .manager import MemoryManager

        globals()["MemoryManager"] = MemoryManager
        return MemoryManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
