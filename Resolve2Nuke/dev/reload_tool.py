import importlib
import os
import sys


def reload_resolve2nuke(prints: bool = True, extra_loop: bool = True) -> None:
    """
    Reload all Resolve2Nuke modules. Useful for development.
    """
    import Resolve2Nuke
    any_fails = False

    package_name = Resolve2Nuke.__name__
    package_path = Resolve2Nuke.__path__[0]
    parent_path = os.path.dirname(package_path)

    module_names = []
    for root, dirs, files in os.walk(package_path):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__')
        for filename in sorted(files):
            if not filename.endswith('.py'):
                continue
            rel = os.path.relpath(os.path.join(root, filename), parent_path)
            module_name = rel.replace(os.sep, '.')[:-3]
            if module_name.endswith('.__init__'):
                module_name = module_name[:-9]
            if module_name == package_name:
                continue
            module_names.append(module_name)

    for module_name in module_names:
        try:
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])
            else:
                importlib.import_module(module_name)
            if prints:
                print("Reloaded: {}".format(module_name))
        except Exception as e:
            if prints:
                print("Failed to reload {}: {}".format(module_name, e))
                any_fails = True
    
    if extra_loop:
        if prints:
            print(">> Running additional reload...")
        reload_resolve2nuke(prints=False, extra_loop=False)
    
    if any_fails:
        print(">> WARNING! Some modules failed to reload. Check the output above for details.")
    
    if prints:
        print('\n')
        print(">> Reload finished.")
