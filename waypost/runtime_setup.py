import os
from pathlib import Path


_DLL_DIRECTORY_HANDLES = []
_REGISTERED_DLL_DIRECTORIES = set()


def load_local_env() -> None:
    """Load key=value pairs from the repo-local .env file into os.environ."""
    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / '.env'
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ[key] = value


def configure_windows_fontconfig() -> None:
    """Provide a local Fontconfig config on Windows for WeasyPrint usage."""
    if os.name != "nt":
        return

    if os.environ.get("FONTCONFIG_FILE"):
        return

    project_root = Path(__file__).resolve().parent.parent
    fontconfig_dir = project_root / "runtime" / "fontconfig"
    fontconfig_file = fontconfig_dir / "fonts.conf"
    if not fontconfig_file.exists():
        return

    local_app_data = Path(os.environ.get("LOCALAPPDATA", str(project_root)))
    cache_home = local_app_data / "waypost-fontconfig-cache"
    cache_home.mkdir(parents=True, exist_ok=True)

    os.environ.setdefault("XDG_CACHE_HOME", str(cache_home))
    os.environ.setdefault("FONTCONFIG_PATH", str(fontconfig_dir))
    os.environ.setdefault("FONTCONFIG_FILE", str(fontconfig_file))


def configure_windows_weasyprint_runtime() -> None:
    """Register DLL lookup paths for WeasyPrint native dependencies on Windows."""
    if os.name != 'nt':
        return

    candidate_dirs = []
    raw_directories = os.environ.get('WEASYPRINT_DLL_DIRECTORIES', '')
    if raw_directories:
        candidate_dirs.extend(part.strip() for part in raw_directories.split(os.pathsep) if part.strip())

    single_directory = os.environ.get('WEASYPRINT_DLL_DIR', '').strip()
    if single_directory:
        candidate_dirs.append(single_directory)

    conda_prefix = os.environ.get('CONDA_PREFIX', '').strip()
    if conda_prefix:
        candidate_dirs.append(str(Path(conda_prefix) / 'Library' / 'bin'))

    msys2_root = os.environ.get('MSYS2_ROOT', '').strip()
    if msys2_root:
        msys2_base = Path(msys2_root)
    else:
        msys2_base = Path('C:/msys64')

    candidate_dirs.extend(
        str(msys2_base / runtime_name / 'bin')
        for runtime_name in ('ucrt64', 'mingw64', 'clang64')
    )

    for raw_path in candidate_dirs:
        dll_path = Path(raw_path)
        if not dll_path.exists():
            continue

        normalized_path = str(dll_path.resolve())
        if normalized_path in _REGISTERED_DLL_DIRECTORIES:
            continue

        os.environ['PATH'] = normalized_path + os.pathsep + os.environ.get('PATH', '')
        if hasattr(os, 'add_dll_directory'):
            _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(normalized_path))
        _REGISTERED_DLL_DIRECTORIES.add(normalized_path)
