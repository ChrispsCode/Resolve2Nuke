"""
Resolve2Nuke menu.py

Nuke must always launch, even when the tool is broken or unsupported: green
line on success, red line plus traceback on failure.
"""


class SkipImport(Exception):
    """Quietly skip loading the tool (the reason has already been printed)."""


# # 17+ only: 15.2's BRAW reader is SDK 2.2 and it can't import Resolve's scripting module.
MIN_NUKE_MAJOR = 17

try:
    import nuke

    if nuke.NUKE_VERSION_MAJOR < MIN_NUKE_MAJOR:
        print(f"\033[31m[R2N] Warning: Resolve2Nuke needs Nuke {MIN_NUKE_MAJOR}+, "
              f"this is Nuke {nuke.NUKE_VERSION_MAJOR}. Resolve2Nuke will be missing in this Nuke!\033[0m")
        raise SkipImport

    import Resolve2Nuke
    from Resolve2Nuke import nuke_menu

    nuke_menu.build()
    print(f"\033[32m[R2N] Resolve2Nuke v{Resolve2Nuke.__version__} loaded successfully.\033[0m")

except SkipImport:
    pass

except ImportError:
    import traceback
    print("\033[31m[R2N] Resolve2Nuke import failed:")
    traceback.print_exc()
    print("\033[0m")

except Exception:
    import traceback
    print("\033[31m[R2N] Resolve2Nuke menu setup failed:")
    traceback.print_exc()
    print("\033[0m")
